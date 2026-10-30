#!/usr/bin/env python3
"""按角色执行 Paper Pulse 页面级浏览器走查并生成 JSON 报告。"""

from __future__ import annotations

import argparse
import itertools
import json
import os
import re
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit
from urllib.request import Request, urlopen


ROOT_DIR = Path(__file__).resolve().parents[1]
APP_DIR = ROOT_DIR / "frontend" / "src" / "app"
EXPECTED_ROLES = ("admin", "reviewer", "viewer", "limited")
ROLE_USERNAME_ALIASES = {"limited": ("limited",)}
SKIP_LABEL_RE = re.compile(r"删除|清空|退出登录|取消发布|重置|发布")
WRITE_ACTION_RE = re.compile(
    r"添加|新增|新建|保存|更新|编辑|修改|绑定|解绑|验证|审核|通过|驳回|拒绝|"
    r"上传|导入|生成|提交|合并|分配|重试|同步|启用|禁用|标记|确认|执行|创建|授权"
)
ERROR_TEXT_RE = re.compile(r"失败|错误|出错|异常|\bError\b|\berror\b", re.IGNORECASE)
EMPTY_TEXT_RE = re.compile(r"暂无|没有|无数据|空状态|未找到|暂无论文|暂无作者|暂无视频|暂无草稿")
SENSITIVE_QUERY_KEYS = {"access_token", "token", "authorization", "api_key", "key", "secret", "password", "auth_code", "email", "mail"}


def redact_url(url: str | None) -> str | None:
    if not url:
        return url
    try:
        parsed = urlsplit(url)
        query = [(key, "[已隐藏]") if key.lower() in SENSITIVE_QUERY_KEYS else (key, value)
                 for key, value in parse_qsl(parsed.query, keep_blank_values=True)]
        return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urlencode(query), parsed.fragment))
    except Exception:
        return url


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True, help="前端地址，例如 http://localhost:3000")
    parser.add_argument("--api-url", required=True, help="API 地址，例如 http://127.0.0.1:8000")
    parser.add_argument("--report", default="out.json", help="JSON 报告路径（默认 out.json）")
    parser.add_argument("--screens", default="screens", help="问题截图目录（默认 screens）")
    parser.add_argument("--expect-empty", action="store_true", help="以空数据库场景检查提示与错误状态")
    parser.add_argument("--headed", action="store_true", help="显示浏览器窗口（默认无头运行，不占用鼠标和屏幕）")
    parser.add_argument("--action-timeout", type=int, default=6000, help="单次操作超时毫秒数")
    return parser.parse_args()


def read_users() -> dict[str, str]:
    raw = os.environ.get("E2E_USERS")
    if not raw:
        raise ValueError("请通过 E2E_USERS 环境变量提供四种角色的 JSON 密码映射")
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError("E2E_USERS 必须是 JSON 对象")
    missing = [role for role in EXPECTED_ROLES if not isinstance(value.get(role), str) or not value[role]]
    if missing:
        raise ValueError(f"E2E_USERS 缺少有效密码项：{', '.join(missing)}")
    return {role: value[role] for role in EXPECTED_ROLES}


def discover_routes() -> list[str]:
    """发现应用分组页面及独立设置子页面，忽略登录页等公开页面。"""
    routes: set[str] = set()
    for page_file in APP_DIR.rglob("page.tsx"):
        relative = page_file.relative_to(APP_DIR)
        parts = relative.parts[:-1]
        if "(app)" in parts:
            route_parts = [part for part in parts if not (part.startswith("(") and part.endswith(")"))]
            routes.add("/" + "/".join(route_parts))
        elif parts and parts[0] == "settings":
            routes.add("/" + "/".join(parts))
    return sorted(routes)


def http_json(base_url: str, path: str, token: str | None = None, method: str = "GET",
              body: Any = None, timeout: float = 25) -> tuple[int, Any, str | None]:
    url = urljoin(base_url.rstrip("/") + "/", path.lstrip("/"))
    payload = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
    headers = {"Accept": "application/json"}
    if payload is not None:
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = Request(url, data=payload, method=method, headers=headers)
    try:
        with urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8", errors="replace")
            try:
                return response.status, json.loads(raw), url
            except json.JSONDecodeError:
                return response.status, raw, url
    except HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            data = raw
        return exc.code, data, url
    except (URLError, TimeoutError, OSError) as exc:
        return 0, None, f"{url} ({exc})"


def api_login(api_url: str, role: str, password: str) -> tuple[str | None, str, int]:
    usernames = ROLE_USERNAME_ALIASES.get(role, (role,))
    last_status = 0
    last_message = ""
    for username in usernames:
        status, data, _ = http_json(api_url, "/api/auth/login", method="POST",
                                    body={"username": username, "password": password})
        last_status = status
        if status == 200 and isinstance(data, dict) and data.get("access_token"):
            return str(data["access_token"]), username, status
        if status != 401:
            last_message = f"登录接口返回 HTTP {status}"
            break
        last_message = "登录接口拒绝了凭证"
    return None, last_message, last_status


class Walkthrough:
    def __init__(self, args: argparse.Namespace, routes: list[str], report: dict[str, Any]) -> None:
        self.args = args
        self.base_url = args.base_url.rstrip("/")
        self.api_url = args.api_url.rstrip("/")
        self.routes = routes
        self.report = report
        self.screens_dir = Path(args.screens).expanduser().resolve()
        self.screens_dir.mkdir(parents=True, exist_ok=True)
        self.screenshot_number = 0
        self.context: Any = None
        self.page: Any = None
        self.role = ""
        self.route = ""
        self.current_action = "page-load"
        self.event_log: list[dict[str, Any]] = []
        self.seen_issues: set[tuple[Any, ...]] = set()
        self.suppress_http_errors = False
        self._page_cache: dict[str, dict[str, Any]] = {}

    def page_record(self, route: str) -> dict[str, Any]:
        route = route or "/"
        record = self._page_cache.get(route)
        if record is None:
            record = {"route": route, "visible_controls": [], "visible_write_actions": [],
                      "operations": [], "issues": []}
            self._page_cache[route] = record
            self.report["roles"][self.role]["pages"].append(record)
        return record

    def screenshot(self) -> str | None:
        if self.page is None or self.page.is_closed():
            return None
        self.screenshot_number += 1
        name = f"{self.role}-{self.screenshot_number:05d}.png"
        path = self.screens_dir / name
        try:
            self.page.screenshot(path=str(path), full_page=True, timeout=5000)
            return str(path)
        except Exception:
            try:
                self.page.screenshot(path=str(path), full_page=False, timeout=2500)
                return str(path)
            except Exception:
                return None

    def issue(self, route: str, kind: str, operation: str, detail: Any,
              *, http_status: int | None = None, url: str | None = None,
              method: str | None = None) -> None:
        url = redact_url(url)
        key = (self.role, route, kind, operation, http_status, method, url, str(detail)[:180])
        if key in self.seen_issues:
            return
        self.seen_issues.add(key)
        row: dict[str, Any] = {
            "type": kind,
            "operation": operation,
            "detail": detail,
            "screenshot": self.screenshot(),
        }
        if http_status is not None:
            row["status_code"] = http_status
        if url:
            row["url"] = url
        if method:
            row["method"] = method
        self.page_record(route)["issues"].append(row)

    def _on_console(self, message: Any) -> None:
        if message.type == "error":
            self.event_log.append({"type": "console_error", "text": message.text,
                                   "route": self.route, "operation": self.current_action})

    def _on_page_error(self, error: Exception) -> None:
        self.event_log.append({"type": "crash", "text": str(error),
                               "route": self.route, "operation": self.current_action})

    def _on_response(self, response: Any) -> None:
        if response.status >= 400 and not self.suppress_http_errors:
            self.event_log.append({
                "type": "http_error", "text": f"HTTP {response.status}",
                "route": self.route, "operation": self.current_action,
                "status": response.status, "url": response.url,
                "method": response.request.method,
            })

    def _on_request_failed(self, request: Any) -> None:
        failure = request.failure or "request failed"
        if "ERR_ABORTED" in str(failure):
            return
        self.event_log.append({
            "type": "http_error", "text": str(failure), "route": self.route,
            "operation": self.current_action, "url": request.url, "method": request.method,
        })

    def attach_page_events(self) -> None:
        self.page.on("console", self._on_console)
        self.page.on("pageerror", self._on_page_error)
        self.page.on("response", self._on_response)
        self.page.on("requestfailed", self._on_request_failed)
        self.page.on("dialog", lambda dialog: dialog.dismiss())
        self.page.on("popup", lambda popup: popup.close())

    def drain_events(self, route: str, start: int) -> list[dict[str, Any]]:
        found = self.event_log[start:]
        for event in found:
            kind = str(event["type"])
            self.issue(
                str(event.get("route") or route), kind, str(event.get("operation") or self.current_action),
                event.get("text"), http_status=event.get("status"), url=event.get("url"),
                method=event.get("method"),
            )
        return found

    def go(self, route: str, query: str = "") -> bool:
        self.route = route
        self.current_action = "page-load"
        event_start = len(self.event_log)
        target = self.base_url + route + (query if query.startswith("?") else ("?" + query if query else ""))
        try:
            self.page.goto(target, wait_until="domcontentloaded", timeout=20000)
            self.page.wait_for_timeout(700)
            self.route = urlsplit(self.page.url).path or route
            self.drain_events(route, event_start)
            return True
        except Exception as exc:
            self.issue(route, "hang", "page-load", str(exc))
            self.drain_events(route, event_start)
            return False

    def handle_custom_dialog(self) -> None:
        try:
            dialogs = self.page.locator('[role="dialog"]')
            for idx in range(dialogs.count() - 1, -1, -1):
                dialog = dialogs.nth(idx)
                if not dialog.is_visible():
                    continue
                cancel = dialog.get_by_role("button", name=re.compile(r"取消|关闭|返回|稍后", re.I))
                if cancel.count() and cancel.first.is_visible():
                    cancel.first.click(timeout=1800)
                else:
                    self.page.keyboard.press("Escape")
                self.page.wait_for_timeout(120)
        except Exception:
            try:
                self.page.keyboard.press("Escape")
            except Exception:
                pass

    def visible_actions(self) -> list[dict[str, Any]]:
        script = r"""() => {
          const nodes = [...document.querySelectorAll('button,a[href],[role="button"],[role="link"],[role="tab"],[role="combobox"],[role="menuitem"],select')];
          const seen = new Set();
          const out = [];
          for (const el of nodes) {
            if (seen.has(el)) continue;
            seen.add(el);
            const s = getComputedStyle(el), r = el.getBoundingClientRect();
            if (s.display === 'none' || s.visibility === 'hidden' || r.width < 1 || r.height < 1) continue;
            const tag = el.tagName.toLowerCase();
            const role = el.getAttribute('role') || (tag === 'a' ? 'link' : tag === 'select' ? 'combobox' : tag === 'button' ? 'button' : 'button');
            const name = (el.getAttribute('aria-label') || el.innerText || el.getAttribute('title') || el.value || '').replace(/\s+/g,' ').trim().slice(0,120);
            const href = el.getAttribute('href') || '';
            const disabled = el.hasAttribute('disabled') || el.getAttribute('aria-disabled') === 'true';
            const pathParts = [];
            let cur = el;
            while (cur && cur.nodeType === 1 && cur !== document.documentElement) {
              if (cur.id) { pathParts.unshift('#' + CSS.escape(cur.id)); break; }
              let part = cur.tagName.toLowerCase();
              const parent = cur.parentElement;
              if (parent) {
                const same = [...parent.children].filter(x => x.tagName === cur.tagName);
                if (same.length > 1) part += `:nth-of-type(${same.indexOf(cur)+1})`;
              }
              pathParts.unshift(part); cur = parent;
            }
            out.push({role,name,href,disabled,tag,path:pathParts.join(' > '),
              occurrence: out.filter(x => x.role === role && x.name === name).length});
          }
          return out;
        }"""
        try:
            return self.page.evaluate(script)
        except Exception:
            return []

    def locate_action(self, item: dict[str, Any]) -> Any:
        role, name = item["role"], item["name"]
        if name:
            locator = self.page.get_by_role(role, name=name, exact=True)
            if locator.count() > int(item["occurrence"]):
                return locator.nth(int(item["occurrence"]))
        return self.page.locator(item["path"])

    def action_is_external(self, item: dict[str, Any]) -> bool:
        href = item.get("href", "")
        if not href or href.startswith(("#", "javascript:")) or (href.startswith("/") and not href.startswith("//")):
            return False
        parsed = urlsplit(href)
        if not parsed.scheme and not parsed.netloc:
            return False
        base = urlsplit(self.base_url)
        return f"{parsed.scheme}://{parsed.netloc}" != f"{base.scheme}://{base.netloc}"

    def audit_visible_actions(self, route: str) -> None:
        record = self.page_record(route)
        items = self.visible_actions()
        record["visible_controls"] = [
            {
                "role": item["role"],
                "label": item["name"],
                "href": item.get("href") or None,
                "disabled": bool(item.get("disabled")),
                "write_operation": bool(item["role"] in {"button", "menuitem"} and WRITE_ACTION_RE.search(item["name"])),
                "skipped_as_destructive": bool(SKIP_LABEL_RE.search(item["name"])),
            }
            for item in items
        ]
        for item in items:
            label = item["name"] or f"<{item['role']}>"
            if item.get("disabled"):
                continue
            if self.action_is_external(item) or SKIP_LABEL_RE.search(label):
                continue
            if item["role"] in {"button", "menuitem"} and WRITE_ACTION_RE.search(label):
                action = {"role": item["role"], "label": label, "href": item.get("href") or None}
                if action not in record["visible_write_actions"]:
                    record["visible_write_actions"].append(action)

        original_url = self.page.url
        for number, item in enumerate(items, start=1):
            label = item["name"] or f"<{item['role']} #{number}>"
            operation = f"click {item['role']}: {label}"
            if item.get("disabled") or self.action_is_external(item) or SKIP_LABEL_RE.search(label):
                continue
            try:
                target = self.locate_action(item)
                if not target.count() or not target.first.is_visible():
                    continue
            except Exception:
                continue
            self.current_action = operation
            event_start = len(self.event_log)
            try:
                target.first.click(timeout=self.args.action_timeout)
                self.page.wait_for_timeout(500)
                if item["role"] == "combobox":
                    self.page.keyboard.press("Escape")
                self.handle_custom_dialog()
            except Exception as exc:
                kind = "hang" if "Timeout" in type(exc).__name__ or "timeout" in str(exc).lower() else "crash"
                self.issue(route, kind, operation, str(exc))
            events = self.drain_events(route, event_start)
            is_write = bool(WRITE_ACTION_RE.search(label)) and item["role"] in {"button", "menuitem"}
            if is_write:
                for event in events:
                    if event.get("type") == "http_error" and event.get("status") == 403 and event.get("method", "GET").upper() not in {"GET", "HEAD", "OPTIONS"}:
                        self.issue(route, "permission_mismatch", operation,
                                   "写操作按钮可见，但对应写接口返回 403。",
                                   http_status=403, url=event.get("url"), method=event.get("method"))
            self.check_ui_error_text(route, operation)
            try:
                if self.page.url != original_url:
                    self.go(urlsplit(original_url).path, "?" + urlsplit(original_url).query if urlsplit(original_url).query else "")
            except Exception:
                self.go(route)
        record["operations"].extend(
            {"kind": "click", "role": item["role"], "label": item["name"], "skipped": bool(SKIP_LABEL_RE.search(item["name"]))}
            for item in items
        )

    def check_ui_error_text(self, route: str, operation: str) -> None:
        try:
            visible_alerts = self.page.locator(
                '[role="alert"], [data-sonner-toast], [data-state="open"][role="status"], '
                '[class*="text-destructive"], [class*="text-red-"]'
            )
            texts: list[str] = []
            for idx in range(min(visible_alerts.count(), 8)):
                el = visible_alerts.nth(idx)
                if el.is_visible():
                    text = el.inner_text(timeout=500).strip()
                    if text and ERROR_TEXT_RE.search(text):
                        texts.append(text)
            for text in texts:
                self.issue(route, "ui_error_text", operation, text[:500])
        except Exception:
            pass

    def login(self, role: str, username: str, password: str, token: str | None) -> bool:
        self.role = role
        self._page_cache = {}
        self.event_log = []
        self.page = self.context.new_page()
        self.attach_page_events()
        self.route = "/login"
        event_start = len(self.event_log)
        try:
            self.page.goto(self.base_url + "/login", wait_until="domcontentloaded", timeout=20000)
            self.page.get_by_label("用户名").fill(username)
            self.page.get_by_label("密码").fill(password)
            self.page.get_by_role("button", name=re.compile("登录")).click(timeout=10000)
            self.page.wait_for_url(lambda url: urlsplit(str(url)).path != "/login", timeout=20000)
            self.page.wait_for_timeout(600)
        except Exception as exc:
            self.issue("/login", "crash", "登录", str(exc))
            self.drain_events("/login", event_start)
            return False
        self.drain_events("/login", event_start)
        if token is None:
            self.issue("/login", "crash", "登录", "API 凭证验证失败，无法获取角色 token。")
            return False
        return True

    def get_json(self, route: str, path: str, token: str, *, operation: str,
                 method: str = "GET", body: Any = None) -> tuple[int, Any, str | None]:
        status, data, url = http_json(self.api_url, path, token, method, body)
        if status == 0 or status >= 400:
            self.issue(route, "http_error", operation, data if data is not None else "请求失败",
                       http_status=status or None, url=url, method=method)
        return status, data, url

    def run_pagination(self, token: str) -> dict[str, Any] | None:
        route = "/papers"
        self.route = route
        record = self.page_record(route)
        status, first, _ = self.get_json(route, "/api/papers?page=1&page_size=20", token,
                                         operation="分页:读取首页")
        if status != 200 or not isinstance(first, dict):
            return None
        total, page_size = first.get("total"), first.get("page_size", 20)
        if not isinstance(total, int) or total < 0 or not isinstance(page_size, int) or page_size <= 0:
            self.issue(route, "ui_error_text", "分页:检查计数", f"分页计数非法：total={total!r}, page_size={page_size!r}")
            return first
        last_page = max(1, (total + page_size - 1) // page_size)
        for requested_page in (last_page, last_page + 1000):
            operation = f"分页:第 {requested_page} 页"
            self.current_action = operation
            self.go(route, f"?page={requested_page}")
            event_start = len(self.event_log)
            api_status, data, _ = self.get_json(
                route, f"/api/papers?page={requested_page}&page_size={page_size}", token,
                operation=operation,
            )
            self.page.wait_for_timeout(600)
            self.drain_events(route, event_start)
            self.check_ui_error_text(route, operation)
            try:
                body_text = self.page.locator("body").inner_text(timeout=1000)
                if re.search(r"加载中|正在加载", body_text):
                    self.page.wait_for_timeout(1800)
                    body_text = self.page.locator("body").inner_text(timeout=1000)
                    if re.search(r"加载中|正在加载", body_text):
                        self.issue(route, "hang", operation, "分页导航后页面仍停留在加载状态。")
            except Exception:
                pass
            if api_status == 200 and isinstance(data, dict):
                items = data.get("items")
                if not isinstance(items, list) or len(items) > page_size:
                    self.issue(route, "ui_error_text", operation, "分页返回的 items 数量与 page_size 不一致。")
                if data.get("total", -1) < 0:
                    self.issue(route, "ui_error_text", operation, "分页总数小于 0。")
                if requested_page == last_page and data.get("total") != total:
                    self.issue(route, "ui_error_text", operation, "首页和最后一页的结果总数不一致。")
            record["operations"].append({"kind": "pagination", "page": requested_page,
                                         "http_status": api_status, "total": total})
        return first

    @staticmethod
    def _visible_filter_controls(page: Any) -> list[dict[str, Any]]:
        try:
            return page.evaluate(r"""() => [...document.querySelectorAll('select')].filter(el => {
              const r=el.getBoundingClientRect(), s=getComputedStyle(el);
              return r.width>0 && r.height>0 && s.display!=='none' && s.visibility!=='hidden';
            }).map(el => {
              let label = el.getAttribute('aria-label') || el.labels?.[0]?.innerText || '';
              let node = el;
              for (let i=0; i<4 && node && !label; i++,node=node.parentElement) {
                const candidate=node.querySelector(':scope > label');
                if (candidate) label=candidate.innerText;
              }
              return {label:(label||'').replace(/\s+/g,' ').trim(),
                options:[...el.options].map(o=>({value:o.value,label:o.textContent.trim()})).filter(o=>o.value)};
            })""")
        except Exception:
            return []

    def run_filter_combinations(self, token: str, first_page: dict[str, Any] | None) -> None:
        route = "/papers"
        self.go(route)
        controls = self._visible_filter_controls(self.page)
        groups: list[tuple[str, list[tuple[str, str]]]] = []
        control_specs = [
            (re.compile("课题"), "entity_label"),
            (re.compile("研究方向"), "research_topic"),
            (re.compile("研究领域|学科领域"), "research_field"),
            (re.compile("研究设计"), "study_design"),
            (re.compile("被试/疾病"), "subject_disease"),
        ]
        for pattern, param in control_specs:
            candidates = next((c["options"] for c in controls if pattern.search(c.get("label", ""))), [])
            values = [(param, str(option["value"])) for option in candidates[:3]]
            if values:
                groups.append((param, values))

        if first_page:
            years = sorted({int(item["year"]) for item in first_page.get("items", [])
                            if isinstance(item, dict) and str(item.get("year", "")).isdigit()})
            if years:
                groups.append(("year", [("year_from", str(y)) for y in years[:3]]))
            journals = sorted({str(item.get("journal_name")) for item in first_page.get("items", [])
                               if isinstance(item, dict) and item.get("journal_name")})
            if journals:
                groups.append(("journal", [("journal", value) for value in journals[:3]]))

        lifecycle_options = [
            ("review_outcome", "unreviewed"),
            ("review_outcome", "approved"),
            ("review_outcome", "excluded"),
            ("has_pdf", "false"),
            ("metadata_complete", "false"),
        ]
        groups.append(("lifecycle", lifecycle_options))

        group_lookup = {name: options[:8 if name == "lifecycle" else 3] for name, options in groups}
        priority = {name: index for index, name in enumerate((
            "lifecycle", "year",
            "entity_label", "journal", "research_field", "research_topic"
        ))}
        ordered_groups = sorted(group_lookup.items(), key=lambda entry: (priority.get(entry[0], 100), entry[0]))
        pair_products = []
        for (left_name, left_values), (right_name, right_values) in itertools.combinations(ordered_groups, 2):
            values = list(itertools.product(left_values, right_values))
            pair_products.append((left_name, right_name, values))
        combinations: list[tuple[str, list[tuple[str, str]]]] = []
        # 先覆盖每一对筛选维度，再轮流增加不同取值，最多执行 30 组。
        for variant in range(max((len(values) for _, _, values in pair_products), default=0)):
            for left_name, right_name, values in pair_products:
                if variant < len(values):
                    combinations.append((f"{left_name}+{right_name}", list(values[variant])))
                    if len(combinations) >= 30:
                        break
            if len(combinations) >= 30:
                break
        record = self.page_record(route)
        if not combinations:
            self.report["roles"][self.role]["notes"].append("未从当前论文页读取到足够筛选项，筛选组合检查跳过。")
        for index, (label, params) in enumerate(combinations, start=1):
            query = {key: value for key, value in params}
            encoded = urlencode({**query, "page": 1, "page_size": 20})
            operation = f"筛选组合#{index} {label}"
            self.route, self.current_action = route, operation
            self.go(route, "?" + urlencode(query))
            start = len(self.event_log)
            status, data, _ = self.get_json(route, f"/api/papers?{encoded}", token, operation=operation)
            self.page.wait_for_timeout(350)
            self.drain_events(route, start)
            if status == 200 and isinstance(data, dict):
                total = data.get("total")
                items = data.get("items")
                if not isinstance(total, int) or total < 0 or not isinstance(items, list) or len(items) > 20 or (isinstance(total, int) and total < len(items)):
                    self.issue(route, "ui_error_text", operation,
                               f"筛选结果计数非法：total={total!r}, items={len(items) if isinstance(items, list) else 'invalid'}")
                if isinstance(items, list) and items:
                    mismatches: list[str] = []
                    for paper in items:
                        if "entity_label" in query and paper.get("entity_label") != query["entity_label"]:
                            mismatches.append("entity_label")
                        if "journal" in query and query["journal"].casefold() not in str(paper.get("journal_name") or "").casefold():
                            mismatches.append("journal")
                        if "year_from" in query and (paper.get("year") is None or int(paper["year"]) < int(query["year_from"])):
                            mismatches.append("year_from")
                        if "review_outcome" in query and paper.get("review_outcome") != query["review_outcome"]:
                            mismatches.append("review_outcome")
                        if "has_pdf" in query and bool(paper.get("has_pdf")) != (query["has_pdf"].lower() == "true"):
                            mismatches.append("has_pdf")
                        if "metadata_complete" in query and bool(paper.get("metadata_complete")) != (query["metadata_complete"].lower() == "true"):
                            mismatches.append("metadata_complete")
                        if "research_field" in query and query["research_field"].casefold() not in str(paper.get("research_fields") or "").casefold():
                            mismatches.append("research_field")
                        if "research_topic" in query:
                            topics = paper.get("research_topics") or []
                            if query["research_topic"] not in topics and query["research_topic"] not in str(paper.get("research_fields") or ""):
                                mismatches.append("research_topic")
                    if mismatches:
                        self.issue(route, "ui_error_text", operation,
                                   "筛选返回结果未满足所选条件：" + ", ".join(sorted(set(mismatches))))
                page2_query = urlencode({**query, "page": 2, "page_size": 20})
                page2_status, page2, _ = self.get_json(route, f"/api/papers?{page2_query}", token,
                                                        operation=operation + ":翻页")
                if page2_status == 200 and isinstance(page2, dict) and page2.get("total") != total:
                    self.issue(route, "ui_error_text", operation, "筛选前后翻页的 total 不一致。")
            record["operations"].append({"kind": "filter_combination", "label": label,
                                         "params": query, "http_status": status})

    def run_search_checks(self, token: str) -> None:
        cases = ["定量", "磁共振", "北京", "research methods", "%", "_", "'", '"', "🧪", "长" * 300]
        for route in ("/papers", "/authors"):
            self.go(route)
            if route == "/papers":
                # Desktop filter panel search ("作者、单位、期刊、标题、DOI…") or the mobile toolbar search box.
                candidates = self.page.locator('input[placeholder*="DOI"], input[placeholder*="搜索"], input[type="search"]')
            else:
                candidates = self.page.locator('input[placeholder*="搜索"], input[type="search"]')
            try:
                visible = [candidates.nth(i) for i in range(candidates.count()) if candidates.nth(i).is_visible()]
            except Exception:
                visible = []
            if route == "/authors" and not visible:
                try:
                    mobile_filter = self.page.get_by_role("button", name="筛选")
                    if mobile_filter.count() and mobile_filter.first.is_visible():
                        mobile_filter.first.click(timeout=3000)
                        self.page.wait_for_timeout(100)
                        visible = [
                            candidates.nth(i)
                            for i in range(candidates.count())
                            if candidates.nth(i).is_visible()
                        ]
                except Exception:
                    visible = []
            if not visible:
                self.issue(route, "ui_error_text", "搜索框检查", "页面未显示可用的搜索框。")
                continue
            search_box = visible[0]
            for index, text in enumerate(cases, start=1):
                operation = f"中文搜索#{index} ({route})"
                self.route, self.current_action = route, operation
                event_start = len(self.event_log)
                try:
                    search_box.fill(text, timeout=3000)
                    search_box.press("Enter", timeout=3000)
                    self.page.wait_for_timeout(500)
                    self.drain_events(route, event_start)
                    self.check_ui_error_text(route, operation)
                    if search_box.input_value() != text:
                        self.issue(route, "ui_error_text", operation, "搜索框内容未保留原始输入，可能存在转义或截断问题。")
                except Exception as exc:
                    self.issue(route, "hang", operation, str(exc))

    def run_long_text(self, token: str, first_page: dict[str, Any] | None) -> None:
        route = "/papers"
        if not first_page or not first_page.get("items"):
            self.report["roles"][self.role]["notes"].append("论文库为空，长文本布局检查跳过。")
            return
        item = first_page["items"][0]
        paper_id = item.get("id")
        if not paper_id:
            self.report["roles"][self.role]["notes"].append("首篇论文缺少 id，长文本布局检查跳过。")
            return
        original = item.get("title_zh")
        long_title = "长文本布局走查" * 42  # 294 个汉字，接近 500 字符
        if len(long_title) < 500:
            long_title += "长文本布局走查" * ((500 - len(long_title) + 6) // 7)
        long_title = long_title[:500]
        self.route, self.current_action = route, "长文本:PATCH 临时标题"
        status, body, url = http_json(self.api_url, f"/api/papers/{paper_id}", token, "GET")
        if status != 200:
            if status >= 400 or status == 0:
                self.issue(route, "http_error", "长文本:读取原值", body,
                           http_status=status or None, url=url, method="GET")
            return
        if isinstance(body, dict):
            original = body.get("title_zh")
        changed = False
        try:
            patch_status, patch_body, patch_url = http_json(
                self.api_url, f"/api/papers/{paper_id}", token, "PATCH", {"title_zh": long_title}
            )
            self.report["roles"][self.role]["direct_write_checks"].append({
                "operation": "PATCH /api/papers/{id} title_zh 临时长文本",
                "status_code": patch_status,
                "paper_id": paper_id,
                "restored": False,
            })
            if patch_status == 403:
                visible_edit = any(
                    WRITE_ACTION_RE.search(button.get("label", ""))
                    and re.search(r"编辑|修改|保存|更新", button.get("label", ""))
                    for button in self.page_record(route)["visible_write_actions"]
                )
                if visible_edit:
                    self.issue(route, "permission_mismatch", "长文本:直接 PATCH",
                               "页面显示论文编辑操作，但当前角色的安全 PATCH 返回 403。",
                               http_status=403, url=patch_url, method="PATCH")
            elif patch_status >= 400 or patch_status == 0:
                self.issue(route, "http_error", "长文本:临时更新", patch_body,
                           http_status=patch_status or None, url=patch_url, method="PATCH")
            changed = patch_status == 200
            if not changed:
                return
            self.go(route)
            self.page.wait_for_timeout(900)
            overflow = self.page.evaluate("document.documentElement.scrollWidth > document.documentElement.clientWidth")
            if overflow:
                self.issue(route, "overflow", "长文本:论文列表", "论文列表出现横向溢出。")
            try:
                row = self.page.locator("tbody tr").first
                if row.count() and row.is_visible():
                    row.click(timeout=3000)
                    self.page.wait_for_timeout(500)
                    detail_overflow = self.page.evaluate("document.documentElement.scrollWidth > document.documentElement.clientWidth")
                    if detail_overflow:
                        self.issue(route, "overflow", "长文本:论文详情", "论文详情出现横向溢出。")
                else:
                    self.report["roles"][self.role]["notes"].append("论文列表没有可打开的行，详情页溢出检查跳过。")
            except Exception as exc:
                self.report["roles"][self.role]["notes"].append(f"无法打开论文详情页进行溢出检查：{exc}")
        finally:
            if changed:
                restore_status, _, restore_url = http_json(
                    self.api_url, f"/api/papers/{paper_id}", token, "PATCH", {"title_zh": original}
                )
                if self.report["roles"][self.role]["direct_write_checks"]:
                    self.report["roles"][self.role]["direct_write_checks"][-1]["restored"] = restore_status == 200
                if restore_status >= 400 or restore_status == 0:
                    self.issue(route, "http_error", "长文本:恢复原值",
                               f"恢复 title_zh 失败，HTTP {restore_status}。",
                               http_status=restore_status or None, url=restore_url, method="PATCH")

    def run_empty_state_checks(self) -> None:
        for route in self.routes:
            if route not in {"/papers", "/authors", "/workspace", "/videos"}:
                continue
            self.go(route)
            self.check_ui_error_text(route, "空状态:页面错误检查")
            try:
                body = self.page.locator("body").inner_text(timeout=2500)
            except Exception:
                body = ""
            if not EMPTY_TEXT_RE.search(body):
                self.issue(route, "ui_error_text", "空状态:提示检查", "页面没有发现空状态提示文本。")
            if len(body.strip()) < 20:
                self.issue(route, "crash", "空状态:白屏检查", "页面内容为空或过少，疑似白屏。")

    def run_error_state_check(self, token: str) -> None:
        route = "/papers"
        self.route, self.current_action = route, "错误状态:模拟论文列表 HTTP 500"

        def fail_papers(route: Any, request: Any) -> None:
            if urlsplit(request.url).path.rstrip("/") == "/api/papers":
                route.fulfill(status=500, content_type="application/json",
                              body=json.dumps({"detail": "走查模拟列表服务错误"}, ensure_ascii=False))
            else:
                route.continue_()

        self.suppress_http_errors = True
        try:
            self.page.route("**/api/papers**", fail_papers)
            self.go(route)
            self.page.wait_for_timeout(1800)
            body = self.page.locator("body").inner_text(timeout=2000)
            if not ERROR_TEXT_RE.search(body):
                self.issue(route, "ui_error_text", "错误状态:模拟 HTTP 500", "列表接口返回 500 后页面没有显示错误提示。")
            if len(body.strip()) < 20:
                self.issue(route, "crash", "错误状态:模拟 HTTP 500", "接口错误时页面呈现白屏。")
            elif re.search(r"加载中|正在加载", body) and not ERROR_TEXT_RE.search(body):
                self.issue(route, "hang", "错误状态:模拟 HTTP 500", "接口错误后页面仍停留在加载状态。")
            self.page_record(route)["operations"].append({"kind": "error_state", "simulated_status": 500})
        except Exception as exc:
            self.issue(route, "hang", "错误状态:模拟 HTTP 500", str(exc))
        finally:
            try:
                self.page.unroute("**/api/papers**", fail_papers)
            except Exception:
                pass
            self.suppress_http_errors = False

    def run_role(self, role: str, password: str, browser: Any) -> None:
        self.role = role
        self.report["roles"][role] = {"username": None, "pages": [], "notes": [], "direct_write_checks": []}
        self.context = browser.new_context(viewport={"width": 1440, "height": 1000}, ignore_https_errors=True)
        token, login_value, login_status = api_login(self.api_url, role, password)
        if token:
            username = login_value
            self.report["roles"][role]["username"] = username
        else:
            username = role
            self.report["roles"][role]["notes"].append(f"API 登录失败（HTTP {login_status}）：{login_value}")
        logged_in = self.login(role, username, password, token)
        if not logged_in:
            self.context.close()
            return

        for route in self.routes:
            if self.go(route):
                self.page_record(route)
                self.audit_visible_actions(route)
                self.check_ui_error_text(route, "页面加载")

        # 同一角色持有的 token 只进行读取分页/筛选和可恢复字段写入。
        first_page = self.run_pagination(token) if token else None
        if token:
            self.run_filter_combinations(token, first_page)
            self.run_search_checks(token)
            self.run_long_text(token, first_page)
        self.run_error_state_check(token or "")
        if self.args.expect_empty:
            self.run_empty_state_checks()
        self.context.close()


def print_summary(report: dict[str, Any]) -> None:
    totals: Counter[str] = Counter()
    print("\n走查汇总")
    print("角色       页面   操作   问题")
    print("---------- ----- ------ -----")
    for role, value in report["roles"].items():
        pages = value.get("pages", [])
        issues = [issue for page in pages for issue in page.get("issues", [])]
        counts = Counter(issue["type"] for issue in issues)
        totals.update(counts)
        operations = sum(len(page.get("operations", [])) for page in pages)
        print(f"{role:<10} {len(pages):>5} {operations:>6} {len(issues):>5}")
    print("问题类型：" + (", ".join(f"{key}={value}" for key, value in sorted(totals.items())) or "无"))
    print(f"总问题数：{sum(totals.values())}")


def main() -> int:
    args = parse_args()
    try:
        users = read_users()
    except (ValueError, json.JSONDecodeError) as exc:
        print(f"配置错误：{exc}", file=sys.stderr)
        return 2
    routes = discover_routes()
    report: dict[str, Any] = {
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "base_url": args.base_url,
        "api_url": args.api_url,
        "expect_empty": bool(args.expect_empty),
        "routes": routes,
        "roles": {},
    }
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("缺少 Playwright，请先安装：python -m pip install playwright", file=sys.stderr)
        return 2

    runner = Walkthrough(args, routes, report)
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=not args.headed)
            for role in EXPECTED_ROLES:
                runner.run_role(role, users[role], browser)
            browser.close()
    except Exception as exc:
        report["runner_error"] = str(exc)
        print(f"走查执行异常：{exc}", file=sys.stderr)

    report_path = Path(args.report).expanduser().resolve()
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print_summary(report)
    print(f"JSON 报告：{report_path}")
    print(f"截图目录：{runner.screens_dir}")
    return 1 if report.get("runner_error") else 0


if __name__ == "__main__":
    raise SystemExit(main())
