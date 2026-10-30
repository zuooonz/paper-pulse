"""
多渠道 PDF 下载模块。

优先级链路：
Europe PMC (OA) → Unpaywall (OA) → arXiv → Semantic Scholar → publisher APIs

与现有 paper_library 层对齐：下载成功后直接对接 pdf_import.process_pdf_import
完成归档、文本提取、日期提取、期刊补全等后续流程。
"""
from __future__ import annotations

import logging
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import requests
from sqlalchemy.orm import Session

from core.service_config import contact_user_agent, runtime_config, warn_missing
from core.net_safety import safe_get
from paper_library.models import Paper
from paper_library.pdf_import import process_pdf_import, PdfImportResult
from paper_library.paper_lifecycle import (
    fail_processing, record_download_error, refresh_pdf_flag,
    reset_processing_to_pending, start_processing,
)
from paper_library.doi import normalize_doi
from tools import storage

logger = logging.getLogger(__name__)


def _looks_like_pdf(response_bytes: bytes, content_type: str | None) -> bool:
    """Return whether a response has a PDF signature and is not labeled as HTML."""
    normalized_content_type = (content_type or "").split(";", 1)[0].strip().lower()
    if "html" in normalized_content_type:
        return False
    return response_bytes.startswith(b"%PDF-")


# ── 数据类 ────────────────────────────────────────────────────

@dataclass
class DownloadResult:
    """单个 DOI 的下载结果"""
    success: bool = False
    path: Optional[str] = None
    method: Optional[str] = None
    error: Optional[str] = None


# ── 核心下载器 ────────────────────────────────────────────────

class PdfDownloader:
    """封装多渠道 PDF 下载逻辑。

    Args:
        elsevier_api_key: Elsevier ScienceDirect API Key
        ieee_api_key: IEEE API Key（当前链路中未实际使用，仅保留参数兼容性）
        semantic_scholar_api_key: Semantic Scholar API Key（用于 openAccessPdf 查询）
        unpaywall_email: Unpaywall 注册邮箱（否则 Unpaywall 不可用）
        request_interval: 每次下载后的间隔秒数（防限流）
    """

    def __init__(
        self,
        elsevier_api_key: Optional[str] = None,
        ieee_api_key: Optional[str] = None,
        semantic_scholar_api_key: Optional[str] = None,
        wiley_tdm_token: Optional[str] = None,
        unpaywall_email: Optional[str] = None,
        request_interval: float = 1.0,
        api_endpoints: dict[str, str] | None = None,
    ):
        self.elsevier_api_key = elsevier_api_key or ""
        self.ieee_api_key = ieee_api_key or ""
        self.semantic_scholar_api_key = semantic_scholar_api_key or ""
        self.wiley_tdm_token = wiley_tdm_token or ""
        self.unpaywall_email = unpaywall_email or ""
        self.request_interval = request_interval
        configured_endpoints = (runtime_config().get("api_endpoints", {}) or {})
        self.api_endpoints = dict(configured_endpoints if api_endpoints is None else api_endpoints)

        self._session = requests.Session()
        self._session.headers.update({
            "User-Agent": contact_user_agent(),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        })

    def close(self) -> None:
        """Close the underlying HTTP connection pool."""
        self._session.close()

    def __enter__(self) -> PdfDownloader:
        return self

    def __exit__(self, *_exc_info) -> None:
        self.close()

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            pass

    def _endpoint(self, key: str, env_name: str) -> str:
        return (os.environ.get(env_name) or self.api_endpoints.get(key) or "").strip().rstrip("/")

    # ── 公共入口 ───────────────────────────────────────────────

    def download_by_doi(
        self,
        doi: str,
        save_path: Path,
        skip_oa: bool = False,
        platforms: list[str] | None = None,
    ) -> DownloadResult:
        """按优先级尝试下载单个 DOI 的 PDF。

        链路顺序：
        1. Europe PMC（开放获取）
        2. Unpaywall（开放获取）
        3. Semantic Scholar（补充 OA，旧论文有效）
        4. arXiv（通过 Semantic Scholar 获取 arXiv ID）
        5. Wiley TDM API
        6. Elsevier API（需 API Key）
        7. IEEE Xplore（基于 CrossRef + stamp.jsp）

        Args:
            skip_oa: 为 True 时跳过 Europe PMC 和 Unpaywall，
                     直接尝试出版商 API（适用于已知非 OA 的论文）
            platforms: 可选的平台白名单列表，如 ['unpaywall', 'europepmc']。
                       只尝试列表中指定的平台，按原始优先级顺序。
                       默认 None 表示尝试所有可用平台。
        """
        norm_doi = normalize_doi(doi)
        if not norm_doi:
            return DownloadResult(error=f"无效的 DOI: {doi}")

        save_path = Path(save_path)
        save_path.parent.mkdir(parents=True, exist_ok=True)

        # 平台白名单检查辅助函数
        def _should_try(name: str) -> bool:
            if platforms is None:
                return True
            return name.lower() in [p.lower() for p in platforms]

        errors: list[str] = []

        # 1) Europe PMC
        if not skip_oa and _should_try("europepmc"):
            logger.info("[%s] 尝试 Europe PMC...", norm_doi)
            res = self._try_europepmc(norm_doi, save_path)
            if res.success:
                return res
            logger.info("[%s] Europe PMC 失败: %s", norm_doi, res.error)
            errors.append(f"Europe PMC: {res.error}")

        # 2) Unpaywall
        if not skip_oa and _should_try("unpaywall"):
            logger.info("[%s] 尝试 Unpaywall...", norm_doi)
            res = self._try_unpaywall(norm_doi, save_path)
            if res.success:
                return res
            logger.info("[%s] Unpaywall 失败: %s", norm_doi, res.error)
            errors.append(f"Unpaywall: {res.error}")

        # 3) Semantic Scholar（补充 OA，对旧论文有效）
        if _should_try("semantic_scholar"):
            if self.semantic_scholar_api_key:
                logger.info("[%s] 尝试 Semantic Scholar...", norm_doi)
                res = self._try_semantic_scholar(norm_doi, save_path)
                if res.success:
                    return res
                logger.info("[%s] Semantic Scholar 失败: %s", norm_doi, res.error)
                errors.append(f"Semantic Scholar: {res.error}")
            else:
                logger.info("[%s] 跳过 Semantic Scholar（未配置 API Key）", norm_doi)
                errors.append("Semantic Scholar: 未配置 API Key")

        # 4) arXiv（通过 Semantic Scholar 获取 arXiv ID）
        if _should_try("arxiv"):
            logger.info("[%s] 尝试 arXiv...", norm_doi)
            res = self._try_arxiv(norm_doi, save_path)
            if res.success:
                return res
            logger.info("[%s] arXiv 失败: %s", norm_doi, res.error)
            errors.append(f"arXiv: {res.error}")

        # 5) Springer Direct（OA 直链，不需要 API key）
        if _should_try("springer"):
            logger.info("[%s] 尝试 Springer Direct...", norm_doi)
            res = self._try_springer(norm_doi, save_path)
            if res.success:
                return res
            logger.info("[%s] Springer 失败: %s", norm_doi, res.error)
            errors.append(f"Springer: {res.error}")

        # 6) MDPI CDN（对 MDPI 论文有效，需要 ISSN 映射文件）
        if _should_try("mdpi") and norm_doi.startswith("10.3390/"):
            logger.info("[%s] 尝试 MDPI CDN...", norm_doi)
            res = self._try_mdpi_cdn(norm_doi, save_path)
            if res.success:
                return res
            logger.info("[%s] MDPI CDN 失败: %s", norm_doi, res.error)
            errors.append(f"MDPI: {res.error}")

        # 7) Wiley TDM API
        if _should_try("wiley"):
            if self.wiley_tdm_token:
                logger.info("[%s] 尝试 Wiley TDM...", norm_doi)
                res = self._try_wiley(norm_doi, save_path)
                if res.success:
                    return res
                logger.info("[%s] Wiley TDM 失败: %s", norm_doi, res.error)
                errors.append(f"Wiley: {res.error}")
            else:
                warn_missing("Wiley", "WILEY_TDM_TOKEN")
                logger.info("[%s] 跳过 Wiley TDM（未配置 API Key）", norm_doi)
                errors.append("Wiley: 未配置 API Key")

        # 6) Elsevier API
        if _should_try("elsevier"):
            if self.elsevier_api_key:
                logger.info("[%s] 尝试 Elsevier API...", norm_doi)
                res = self._try_elsevier(norm_doi, save_path)
                if res.success:
                    return res
                logger.info("[%s] Elsevier API 失败: %s", norm_doi, res.error)
                errors.append(f"Elsevier: {res.error}")
            else:
                warn_missing("Elsevier", "ELSEVIER_API_KEY")
                logger.info("[%s] 跳过 Elsevier API（未配置 API Key）", norm_doi)
                errors.append("Elsevier: 未配置 API Key")

        # 8) IEEE Xplore
        if _should_try("ieee"):
            logger.info("[%s] 尝试 IEEE Xplore...", norm_doi)
            res = self._try_ieee(norm_doi, save_path)
            if res.success:
                return res
            logger.info("[%s] IEEE Xplore 失败: %s", norm_doi, res.error)
            errors.append(f"IEEE Xplore: {res.error}")

        summary = "；".join(errors)
        return DownloadResult(error=f"所有渠道均失败（{summary}）")

    def download_for_paper(
        self,
        paper: Paper,
        session: Session,
        workspace: str = ".",
        skip_oa: bool = False,
    ) -> DownloadResult:
        """为已有 Paper 记录下载 PDF 并完成后续导入流程。

        流程：
        1. 检查 paper.doi
        2. 下载到临时文件
        3. 调用 process_pdf_import 完成归档 + 提取 + enrichment
        4. 清理临时文件
        """
        was_pending = paper.processing_status == "pending"
        if not paper.doi:
            record_download_error(paper, "pdf_download_requires_doi")
            refresh_pdf_flag(paper, workspace)
            return DownloadResult(error="Paper 无 DOI")

        norm_doi = normalize_doi(paper.doi)
        if not norm_doi:
            record_download_error(paper, "pdf_download_invalid_doi")
            refresh_pdf_flag(paper, workspace)
            return DownloadResult(error=f"Paper DOI 无效: {paper.doi}")

        # 已有 PDF 则跳过
        if paper.pdf_storage_path:
            resolved = storage.resolve_storage_path(paper.pdf_storage_path, workspace)
            if resolved and resolved.exists():
                refresh_pdf_flag(paper, workspace)
                return DownloadResult(
                    success=True,
                    path=str(resolved),
                    method="existing",
                    error="PDF 已存在",
                )

        ws = Path(workspace).resolve()
        if paper.processing_status == "pending":
            start_processing(paper)
        tmp_dir = storage.get_temp_dir(workspace) / "downloads"
        tmp_dir.mkdir(parents=True, exist_ok=True)
        safe_doi = norm_doi.replace("/", "_").replace("\\", "_")
        tmp_path = tmp_dir / f"{safe_doi}.pdf"

        # 下载
        dl_result = self.download_by_doi(norm_doi, tmp_path, skip_oa=skip_oa)
        if not dl_result.success:
            if was_pending:
                reset_processing_to_pending(paper)
            record_download_error(paper)
            refresh_pdf_flag(paper, workspace)
            return dl_result

        # 调用现有导入流程并保留 internal_id。
        pdf_dir = storage.get_pdf_dir(workspace)
        try:
            import_result: PdfImportResult = process_pdf_import(
                pdf_path=tmp_path,
                session=session,
                next_id=paper.internal_id,
                pdf_dir=pdf_dir,
                workspace=str(ws),
            )
        except Exception as exc:
            logger.exception("process_pdf_import 异常: %s", exc)
            fail_processing(paper, "pdf_processing_failed")
            refresh_pdf_flag(paper, workspace)
            return DownloadResult(error=f"PDF 导入失败: {exc}")
        finally:
            # 清理临时文件（process_pdf_import 已完成归档复制）
            if tmp_path.exists():
                try:
                    tmp_path.unlink()
                except Exception:
                    logger.debug('可选步骤失败，继续执行（download_for_paper）', exc_info=True)
                    pass

        # 根据导入结果判断最终状态
        if import_result.status in ("created", "linked", "replaced", "ok"):
            refresh_pdf_flag(paper, workspace)
            return DownloadResult(
                success=True,
                path=import_result.filename,
                method=dl_result.method,
            )
        else:
            fail_processing(paper, "pdf_processing_failed")
            refresh_pdf_flag(paper, workspace)
            return DownloadResult(
                error=f"PDF 下载成功但导入失败: {import_result.status} - {import_result.message}",
            )

    # ── 内部策略实现 ───────────────────────────────────────────

    def _try_europepmc(self, doi: str, save_path: Path) -> DownloadResult:
        base_url = self._endpoint("europe_pmc_base_url", "EUROPE_PMC_BASE_URL")
        article_base_url = self._endpoint("europe_pmc_article_base_url", "EUROPE_PMC_ARTICLE_BASE_URL")
        if not base_url or not article_base_url:
            warn_missing("Europe PMC", "EUROPE_PMC_BASE_URL and EUROPE_PMC_ARTICLE_BASE_URL")
            return DownloadResult(error="未配置 Europe PMC endpoint")
        try:
            search_url = f"{base_url}/search"
            r = safe_get(self._session,
                search_url,
                params={"query": f"DOI:{doi}", "format": "json", "resultType": "core", "pageSize": 1},
                timeout=20,
            )
            r.raise_for_status()
            data = r.json()
            results = data.get("resultList", {}).get("result", [])
            if not results:
                return DownloadResult(error="Europe PMC 无结果")

            result = results[0]
            pmcid = result.get("pmcid")
            if not pmcid:
                return DownloadResult(error="Europe PMC 无 PMCID")

            # 如果 Europe PMC 未标记有 PDF，跳过下载尝试
            if not result.get("hasPdf"):
                return DownloadResult(error="Europe PMC 无 PDF（仅 HTML 全文）")

            pdf_url = f"{article_base_url}/articles/{pmcid}?pdf=render"
            pdf_r = safe_get(self._session, pdf_url, timeout=30)
            if pdf_r.status_code == 200 and _looks_like_pdf(
                pdf_r.content, pdf_r.headers.get("Content-Type")
            ):
                save_path.write_bytes(pdf_r.content)
                time.sleep(self.request_interval)
                return DownloadResult(success=True, path=str(save_path), method="Europe PMC")
        except Exception as exc:
            return DownloadResult(error=f"Europe PMC 异常: {exc}")
        return DownloadResult(error="Europe PMC 下载失败")

    def _try_unpaywall(self, doi: str, save_path: Path) -> DownloadResult:
        if not self.unpaywall_email:
            warn_missing("Unpaywall", "UNPAYWALL_EMAIL")
            return DownloadResult(error="未配置 Unpaywall Email")
        base_url = self._endpoint("unpaywall_base_url", "UNPAYWALL_BASE_URL")
        if not base_url:
            warn_missing("Unpaywall", "UNPAYWALL_BASE_URL")
            return DownloadResult(error="未配置 Unpaywall endpoint")
        try:
            url = f"{base_url}/{doi}"
            r = safe_get(self._session, url, params={"email": self.unpaywall_email}, timeout=20)
            r.raise_for_status()
            data = r.json()
            best_oa = data.get("best_oa_location")
            if not best_oa:
                return DownloadResult(error="Unpaywall 无 OA 位置")

            pdf_url = best_oa.get("url_for_pdf")
            landing_url = best_oa.get("url")
            if not pdf_url:
                # url_for_pdf 为 None 时，回退到 url 通常是 landing page，不是 PDF
                # 检查 oa_locations 中是否有其他位置提供 PDF
                for loc in data.get("oa_locations", []):
                    alt_pdf = loc.get("url_for_pdf")
                    if alt_pdf:
                        pdf_url = alt_pdf
                        landing_url = loc.get("url", landing_url)
                        break
                if not pdf_url:
                    return DownloadResult(error="Unpaywall 无直接 PDF 链接（仅 landing page）")

            # 带 Referer 下载（部分出版商需要）
            headers = {}
            if landing_url:
                headers["Referer"] = landing_url
            pdf_r = safe_get(self._session, pdf_url, timeout=30, headers=headers)
            if pdf_r.status_code == 200 and _looks_like_pdf(
                pdf_r.content, pdf_r.headers.get("Content-Type")
            ):
                save_path.write_bytes(pdf_r.content)
                time.sleep(self.request_interval)
                return DownloadResult(success=True, path=str(save_path), method="Unpaywall")
        except Exception as exc:
            return DownloadResult(error=f"Unpaywall 异常: {exc}")
        return DownloadResult(error="Unpaywall 下载失败")

    def _try_elsevier(self, doi: str, save_path: Path) -> DownloadResult:
        base_url = self._endpoint("elsevier_base_url", "ELSEVIER_BASE_URL")
        if not self.elsevier_api_key:
            warn_missing("Elsevier", "ELSEVIER_API_KEY")
            return DownloadResult(error="未配置 Elsevier API Key")
        if not base_url:
            warn_missing("Elsevier", "ELSEVIER_BASE_URL")
            return DownloadResult(error="未配置 Elsevier endpoint")
        try:
            url = f"{base_url}/content/article/doi/{doi}"
            headers = {
                "X-ELS-APIKey": self.elsevier_api_key,
                "Accept": "application/pdf",
            }
            r = safe_get(self._session, url, headers=headers, timeout=30)
            if r.status_code == 200 and _looks_like_pdf(
                r.content, r.headers.get("Content-Type")
            ):
                save_path.write_bytes(r.content)
                time.sleep(self.request_interval)
                if self._is_truncated_pdf(save_path):
                    save_path.unlink(missing_ok=True)
                    return DownloadResult(error="Elsevier API 返回截断 PDF（仅有封面页，机构可能无全文订阅权限）")
                return DownloadResult(success=True, path=str(save_path), method="Elsevier API")
        except Exception as exc:
            return DownloadResult(error=f"Elsevier API 异常: {exc}")
        return DownloadResult(error="Elsevier API 下载失败")

    def _try_semantic_scholar(self, doi: str, save_path: Path) -> DownloadResult:
        """通过 Semantic Scholar API 获取 openAccessPdf URL 并下载。"""
        base_url = self._endpoint("semantic_scholar_base_url", "SEMANTIC_SCHOLAR_BASE_URL")
        if not self.semantic_scholar_api_key:
            warn_missing("Semantic Scholar", "SEMANTIC_SCHOLAR_API_KEY")
            return DownloadResult(error="未配置 Semantic Scholar API Key")
        if not base_url:
            warn_missing("Semantic Scholar", "SEMANTIC_SCHOLAR_BASE_URL")
            return DownloadResult(error="未配置 Semantic Scholar endpoint")
        try:
            headers = {}
            if self.semantic_scholar_api_key:
                headers["x-api-key"] = self.semantic_scholar_api_key

            # 1) 查询 Semantic Scholar 获取论文详情
            url = f"{base_url}/paper/DOI:{doi}"
            params = {"fields": "title,openAccessPdf"}
            r = safe_get(self._session, url, params=params, headers=headers, timeout=15)
            if r.status_code != 200:
                return DownloadResult(error=f"Semantic Scholar 查询失败 ({r.status_code})")

            data = r.json()
            oa = data.get("openAccessPdf")
            if not oa or not oa.get("url"):
                return DownloadResult(error="Semantic Scholar 无 OA PDF 信息")

            pdf_url = oa["url"]

            # 2) 过滤 DOI 跳转链接（非直接 PDF）
            parsed = re.sub(r"^https?://", "", pdf_url)
            if parsed.startswith("doi.org/"):
                return DownloadResult(error="Semantic Scholar 返回 DOI 跳转链接，非直接 PDF")

            # 3) 下载 PDF
            pdf_r = safe_get(self._session, pdf_url, timeout=30)
            if pdf_r.status_code == 200 and _looks_like_pdf(
                pdf_r.content, pdf_r.headers.get("Content-Type")
            ):
                save_path.write_bytes(pdf_r.content)
                time.sleep(self.request_interval)
                return DownloadResult(
                    success=True, path=str(save_path), method="Semantic Scholar"
                )
        except Exception as exc:
            return DownloadResult(error=f"Semantic Scholar 异常: {exc}")
        return DownloadResult(error="Semantic Scholar 下载失败")

    def _try_arxiv(self, doi: str, save_path: Path) -> DownloadResult:
        """通过 Semantic Scholar 获取 arXiv ID，然后直链下载 PDF。"""
        s2_base = self._endpoint("semantic_scholar_base_url", "SEMANTIC_SCHOLAR_BASE_URL")
        arxiv_base = self._endpoint("arxiv_base_url", "ARXIV_BASE_URL")
        if not self.semantic_scholar_api_key:
            warn_missing("arXiv lookup", "SEMANTIC_SCHOLAR_API_KEY")
            return DownloadResult(error="未配置 Semantic Scholar API Key")
        if not s2_base or not arxiv_base:
            warn_missing("arXiv", "SEMANTIC_SCHOLAR_BASE_URL and ARXIV_BASE_URL")
            return DownloadResult(error="未配置 arXiv endpoints")
        try:
            headers = {}
            if self.semantic_scholar_api_key:
                headers["x-api-key"] = self.semantic_scholar_api_key

            url = f"{s2_base}/paper/DOI:{doi}"
            params = {"fields": "externalIds"}
            r = safe_get(self._session, url, params=params, headers=headers, timeout=15)
            if r.status_code != 200:
                return DownloadResult(error=f"S2 查询失败 ({r.status_code})")

            data = r.json()
            ext_ids = data.get("externalIds", {})
            arxiv_id = ext_ids.get("ArXiv")
            if not arxiv_id:
                return DownloadResult(error="无 arXiv ID")

            pdf_url = f"{arxiv_base}/{arxiv_id}.pdf"
            pdf_r = safe_get(self._session, pdf_url, timeout=30)
            if pdf_r.status_code == 200 and _looks_like_pdf(
                pdf_r.content, pdf_r.headers.get("Content-Type")
            ):
                save_path.write_bytes(pdf_r.content)
                time.sleep(self.request_interval)
                return DownloadResult(
                    success=True, path=str(save_path), method="arXiv"
                )
        except Exception as exc:
            return DownloadResult(error=f"arXiv 异常: {exc}")
        return DownloadResult(error="arXiv 下载失败")

    def _try_springer(self, doi: str, save_path: Path) -> DownloadResult:
        """通过 Springer PDF 直链下载（对 OA 论文有效，无需 API key）。"""
        base_url = self._endpoint("springer_base_url", "SPRINGER_BASE_URL")
        if not base_url:
            warn_missing("Springer", "SPRINGER_BASE_URL")
            return DownloadResult(error="未配置 Springer endpoint")
        try:
            browser_headers = {
                "User-Agent": contact_user_agent(),
                "Accept": "application/pdf,text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9",
            }
            url = f"{base_url}/{doi}.pdf"
            r = safe_get(self._session, url, headers=browser_headers, timeout=30)
            if r.status_code == 200 and _looks_like_pdf(
                r.content, r.headers.get("Content-Type")
            ):
                save_path.write_bytes(r.content)
                return DownloadResult(success=True, path=str(save_path), method="Springer Direct")
        except Exception as exc:
            return DownloadResult(error=f"Springer 异常: {exc}")
        return DownloadResult(error="Springer 下载失败")

    def _try_mdpi_cdn(self, doi: str, save_path: Path) -> DownloadResult:
        """通过 MDPI CDN (mdpi-res.com) 下载 PDF。

        需要 ISSN → journal_code 映射文件，默认路径：
        the configured temporary root/mdpi_issn_journal_map.json
        """
        if not doi.startswith("10.3390/"):
            return DownloadResult(error="非 MDPI DOI")

        doi_base = self._endpoint("doi_resolver_base_url", "DOI_RESOLVER_BASE_URL")
        mdpi_base = self._endpoint("mdpi_base_url", "MDPI_BASE_URL")
        if not doi_base or not mdpi_base:
            warn_missing("MDPI", "DOI_RESOLVER_BASE_URL and MDPI_BASE_URL")
            return DownloadResult(error="未配置 MDPI endpoints")
        try:
            from curl_cffi import requests as curl_req
        except ImportError:
            return DownloadResult(
                error="缺少 curl-cffi，MDPI 渠道已跳过；请安装 paper-pulse[pdf]"
            )

        import json

        try:
            # 1. DOI 解析获取 landing page URL（使用 curl_cffi 绕过封锁）
            r = safe_get(curl_req, f"{doi_base}/{doi}", impersonate="chrome124", timeout=15)
            if r.status_code != 200:
                return DownloadResult(error=f"DOI 解析失败: {r.status_code}")

            landing_url = r.url
            m = re.search(r"mdpi\.com/(\d{4}-\d{4})/(\d+)/(\d+)/(\d+)", landing_url)
            if not m:
                return DownloadResult(error="无法从 DOI 解析 MDPI 着陆页信息")

            issn, vol, _issue, article = m.group(1), m.group(2), m.group(3), m.group(4)

            # 2. 加载 ISSN → journal_code 映射
            map_path = save_path.parent.parent / "mdpi_issn_journal_map.json"
            if not map_path.exists():
                return DownloadResult(error="MDPI ISSN 映射文件不存在")

            with open(map_path, "r", encoding="utf-8") as f:
                issn_map = json.load(f)

            journal = issn_map.get(issn)
            if not journal:
                return DownloadResult(error=f"ISSN {issn} 无 journal 映射")

            # 3. 尝试 journal code（映射中的）
            article_padded = article.zfill(5)
            article_id = f"{journal}-{vol}-{article_padded}"
            urls_to_try = [
                f"{mdpi_base}/d_attachment/{journal}/{article_id}/article_deploy/{article_id}.pdf",
            ]

            # Fallback: 尝试 DOI code 直接作为 journal code
            doi_suffix = doi.replace("10.3390/", "")
            doi_code = doi_suffix[:-8] if len(doi_suffix) >= 8 else doi_suffix
            if doi_code != journal:
                article_id_fallback = f"{doi_code}-{vol}-{article_padded}"
                urls_to_try.append(
                    f"{mdpi_base}/d_attachment/{doi_code}/{article_id_fallback}/article_deploy/{article_id_fallback}.pdf"
                )

            # 4. 逐个尝试 URL
            for url in urls_to_try:
                try:
                    pdf_r = safe_get(self._session, url, timeout=15)
                    if pdf_r.status_code == 200 and _looks_like_pdf(
                        pdf_r.content, pdf_r.headers.get("Content-Type")
                    ):
                        save_path.write_bytes(pdf_r.content)
                        return DownloadResult(success=True, path=str(save_path), method="MDPI CDN")
                except Exception:
                    logger.debug('可选步骤失败，继续执行（_try_mdpi_cdn）', exc_info=True)
                    continue

            return DownloadResult(error="MDPI CDN 所有 URL 均失败")
        except Exception as exc:
            return DownloadResult(error=f"MDPI CDN 异常: {exc}")
        return DownloadResult(error="MDPI CDN 下载失败")

    def _try_wiley(self, doi: str, save_path: Path) -> DownloadResult:
        """通过 Wiley TDM API 下载 PDF（限速 3 req/s）。"""
        base_url = self._endpoint("wiley_base_url", "WILEY_BASE_URL")
        if not self.wiley_tdm_token:
            warn_missing("Wiley", "WILEY_TDM_TOKEN")
            return DownloadResult(error="未配置 Wiley TDM token")
        if not base_url:
            warn_missing("Wiley", "WILEY_BASE_URL")
            return DownloadResult(error="未配置 Wiley endpoint")
        try:
            headers = {
                "Wiley-TDM-Client-Token": self.wiley_tdm_token,
                "User-Agent": contact_user_agent(),
            }
            url = f"{base_url}/articles/{doi}"
            r = safe_get(self._session, url, headers=headers, timeout=60)
            if r.status_code == 200 and _looks_like_pdf(
                r.content, r.headers.get("Content-Type")
            ):
                save_path.write_bytes(r.content)
                time.sleep(max(self.request_interval, 0.35))  # Wiley 限速 3 req/s
                return DownloadResult(success=True, path=str(save_path), method="Wiley TDM")
            elif r.status_code == 403:
                return DownloadResult(error="Wiley TDM 403（无机构订阅或 IP 被封锁）")
            else:
                return DownloadResult(error=f"Wiley TDM 返回 {r.status_code}")
        except Exception as exc:
            return DownloadResult(error=f"Wiley TDM 异常: {exc}")
        return DownloadResult(error="Wiley TDM 下载失败")

    def _try_ieee(self, doi: str, save_path: Path) -> DownloadResult:
        """通过 IEEE Xplore 下载 PDF（基于 CrossRef + stamp.jsp，无需 API Key）。"""
        crossref_base = self._endpoint("crossref_base_url", "CROSSREF_BASE_URL")
        ieee_base = self._endpoint("ieee_xplore_base_url", "IEEE_XPLORE_BASE_URL")
        if not crossref_base or not ieee_base:
            warn_missing("IEEE Xplore", "CROSSREF_BASE_URL and IEEE_XPLORE_BASE_URL")
            return DownloadResult(error="未配置 IEEE Xplore endpoints")
        try:
            # 使用完整浏览器请求头，避免 418
            browser_headers = {
                "User-Agent": contact_user_agent(),
                "Accept": (
                    "text/html,application/xhtml+xml,application/xml;q=0.9,"
                    "image/avif,image/webp,*/*;q=0.8"
                ),
                "Accept-Language": "en-US,en;q=0.9",
                "Accept-Encoding": "gzip, deflate, br",
                "Connection": "keep-alive",
                "Upgrade-Insecure-Requests": "1",
            }

            # 1) 从 CrossRef 获取 IEEE 链接和 arnumber
            cr_url = f"{crossref_base}/works/{doi}"
            cr_r = safe_get(self._session, cr_url, timeout=15)
            if cr_r.status_code != 200:
                return DownloadResult(error="CrossRef 查询失败")

            data = cr_r.json().get("message", {})
            ieee_link = None
            for link in data.get("link", []):
                url = link.get("URL", "")
                if "ieee" in url.lower():
                    ieee_link = url
                    break

            if not ieee_link:
                return DownloadResult(error="CrossRef 无 IEEE 链接")

            arnumber_match = re.search(r"arnumber=(\d+)", ieee_link)
            if not arnumber_match:
                return DownloadResult(error="无法提取 IEEE arnumber")

            arnumber = arnumber_match.group(1)

            # 3) 访问 document 页面获取 Cookie
            doc_url = f"{ieee_base}/document/{arnumber}"
            safe_get(self._session, doc_url, headers=browser_headers, timeout=15)

            # 4) 访问 stamp.jsp 获取 PDF 嵌入 URL
            stamp_url = f"{ieee_base}/stamp/stamp.jsp?tp=&arnumber={arnumber}"
            stamp_r = safe_get(self._session, stamp_url, headers=browser_headers, timeout=15)
            if stamp_r.status_code != 200:
                return DownloadResult(error=f"stamp.jsp 返回 {stamp_r.status_code}")

            pdf_urls = list(set(
                re.findall(
                    r'src="([^"]+\.pdf[^"]*)"',
                    stamp_r.text,
                    re.IGNORECASE,
                )
            ))
            if not pdf_urls:
                return DownloadResult(error="stamp.jsp 无 PDF 链接")

            for pdf_url in pdf_urls:
                pdf_r = safe_get(self._session, pdf_url, headers=browser_headers, timeout=30)
                if pdf_r.status_code == 200 and _looks_like_pdf(
                    pdf_r.content, pdf_r.headers.get("Content-Type")
                ):
                    save_path.write_bytes(pdf_r.content)
                    time.sleep(self.request_interval)
                    return DownloadResult(
                        success=True, path=str(save_path), method="IEEE Xplore"
                    )
        except Exception as exc:
            return DownloadResult(error=f"IEEE Xplore 异常: {exc}")
        return DownloadResult(error="IEEE Xplore 下载失败")

    # ── 辅助 ────────────────────────────────────────────────────

    @staticmethod
    def _is_truncated_pdf(filepath: Path) -> bool:
        """检查 PDF 是否为截断版本（如 Elsevier 无权限时返回的单页封面）。

        判定条件：只有 1 页且文件小于 500KB。
        """
        try:
            import pymupdf
            doc = pymupdf.open(str(filepath))
            pages = doc.page_count
            doc.close()
            size = filepath.stat().st_size
            if pages == 1 and size < 500 * 1024:
                return True
            return False
        except Exception:
            logger.debug("PDF completeness check failed", exc_info=True)
            return False

