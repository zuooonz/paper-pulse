"""Paper generation API routes."""
import logging

from fastapi import APIRouter

from typing import Any
from paper_library.models import ArticleDraft
from paper_library.models import ChannelVideo
from core.config_loader import ConfigLoader
from fastapi import Depends
from typing import Dict
from fastapi import HTTPException
from typing import List
from core.llm_client import OpenAICompatibleClient
from paper_library.models import Paper
from pathlib import Path
from fastapi import Request
from core.article_builder import build_messages
from paper_library.json_utils import decode_json
from core.domain_config import (
    domain_tags,
    generation_requires_target_entity,
    video_style_prompt,
)
from paper_library.models import draft_papers as dp_tbl
from ...dependencies import get_current_user
from paper_library.db import get_session
from core.entity_detector import get_target_label
import json
from paper_library.cross_db import link_draft_to_paper
from core.domain_config import load_domain
import re
import requests
from sqlalchemy import delete as sa_delete
from core.net_safety import safe_get
from ...config import settings
from ...error_responses import internal_http_error
import time
import urllib.parse
from paper_library.time_utils import utc_now
from paper_library.paper_lifecycle import is_usable_for_generation

from .common import (
    _extract_title,
    _get_ordered_authors,
)

from .schemas import (
    BatchGenerateDraftRequest,
    BatchGenerateWordSummaryRequest,
    BatchReviewRequest,
    GenerateDraftRequest,
    GenerateVideoRequest,
)

logger = logging.getLogger(__name__)
router = APIRouter()

def _delete_drafts_for_paper(session, paper_id: int, *, except_ids: set[int] | None = None) -> None:
    """Delete drafts linked to one paper and all of their associations."""
    old_ids = {
        row[0]
        for row in session.query(dp_tbl.c.draft_id).filter(dp_tbl.c.paper_id == paper_id).all()
        if except_ids is None or row[0] not in except_ids
    }
    if not old_ids:
        return
    session.execute(sa_delete(dp_tbl).where(dp_tbl.c.draft_id.in_(old_ids)))
    session.query(ArticleDraft).filter(ArticleDraft.id.in_(old_ids)).delete(
        synchronize_session=False
    )

@router.post("/{paper_id}/generate-draft")
def generate_draft(
    paper_id: int,
    body: GenerateDraftRequest,
    user: dict = Depends(get_current_user),
):
    workspace = Path(settings.workspace)
    config_dir = settings.config_dir

    with get_session(workspace=settings.workspace, config_dir=config_dir) as session:
        paper = session.get(Paper, paper_id)
        if not paper:
            raise HTTPException(status_code=404, detail="Paper not found")

        previous_article_status = paper.article_status

        # Load configs
        try:
            loader = ConfigLoader(workspace / config_dir)
            cfg = loader.load_all()
        except Exception as e:
            raise internal_http_error(
                logger, "Load paper generation configuration", e,
                status_code=503, detail="服务配置暂不可用，请稍后重试",
            ) from e

        runtime = cfg.get("runtime", {})
        models_cfg = cfg.get("models", {})
        prompts_cfg = cfg.get("prompts", {})
        domain_config = cfg.get("domain") or {}
        if not is_usable_for_generation(
            paper,
            generation_requires_target_entity(domain_config),
            get_target_label(),
        ):
            raise HTTPException(
                status_code=400,
                detail="论文需完成处理、审核通过且符合生成课题范围后才能生成",
            )
        all_model_list = models_cfg.get("models", [])

        def _get_model_cfg(name: str) -> dict:
            for m in all_model_list:
                if m["id"] == name:
                    return m
            return {}

        provider = runtime.get("provider", {})
        base_url = provider.get("base_url", "")
        api_key = provider.get("api_key", "")

        if not base_url or not api_key:
            raise HTTPException(status_code=503, detail="生成服务暂不可用，请检查服务端配置")

        # Init LLM client
        llm = OpenAICompatibleClient(base_url=base_url, api_key=api_key)

        # Determine model
        model_name = body.model
        if not model_name or model_name == "auto":
            routing = models_cfg.get("routing", {})
            auto_seq = routing.get("default_auto_sequence", [])
            all_ids = [m["id"] for m in all_model_list]
            model_name = (
                auto_seq[0]
                if auto_seq
                else (all_ids[0] if all_ids else models_cfg.get("default_model", ""))
            )
        selected_model_cfg = _get_model_cfg(model_name) if model_name else {}
        if not selected_model_cfg:
            raise HTTPException(status_code=503, detail="生成服务暂不可用，请检查服务端配置")

        themes = body.themes or ["临床"]
        profile = body.profile or "default"
        try:
            profile_data = loader.load_profile(profile) if profile != "default" else {}
        except Exception as e:
            raise internal_http_error(
                logger, "Load paper generation profile", e,
                status_code=503, detail="写作配置暂不可用，请检查服务端配置",
            ) from e

        # Build paper rec
        jcr_quartile = ""
        impact_factor = ""
        if paper.journal and paper.journal.metrics:
            latest = sorted(paper.journal.metrics, key=lambda m: m.year or 0, reverse=True)
            if latest:
                metric = latest[0]
                impact_factor = str(metric.impact_factor) if metric.impact_factor is not None else ""
                jcr_quartile = metric.jcr_quartile if metric.jcr_quartile else ""

        ordered = _get_ordered_authors(session, paper.id)
        ca_names = [a.get("name", "") for a in ordered if a.get("is_corresponding")]
        fa_names = [a.get("name", "") for a in ordered if a.get("is_first")]
        rec = {
            "论文编号": str(paper.internal_id or paper.id),
            "API权威标题": paper.title or "",
            "英文标题": paper.title or "",
            "API权威期刊": paper.journal_name or "",
            "期刊名称": paper.journal_name or "",
            "API发表日期": str(paper.year or ""),
            "DOI": paper.doi or "",
            "IF": impact_factor,
            "JCR分区": jcr_quartile,
            "PMID": paper.pmid or "",
            "apa_citation": paper.apa_citation or "",
            "cites_target": paper.cites_target,
            "entity_label": paper.entity_label,
            "通讯作者": "; ".join(ca_names),
            "第一作者": "; ".join(fa_names),
        }

        extracted_text = paper.extracted_text or ""
        image_count = len(paper.extracted_images) if paper.extracted_images and isinstance(paper.extracted_images, list) else 0

        messages = build_messages(
            rec, themes, prompts_cfg, extracted_text, image_count,
            profile=profile, cites_target=paper.cites_target,
            profile_data=profile_data,
        )

        # 配置、凭据和请求上下文均已预检，旧稿仍然完整。
        paper.article_status = "generating"
        session.commit()

        # Call LLM
        try:
            content = llm.chat(model_name, messages, thinking=selected_model_cfg.get("thinking"))
        except Exception as e:
            paper.article_status = previous_article_status
            session.commit()
            raise internal_http_error(
                logger, "Generate paper draft with LLM", e,
                status_code=502, detail="生成服务暂时不可用，请稍后重试",
            ) from e

        try:
            # Extract Chinese title and persist the replacement in one transaction.
            chinese_title = _extract_title(content, paper.title or "")
            draft = ArticleDraft(
                draft_type="single_daily",
                title=chinese_title,
                content=content,
                status="draft",
                meta={
                    "paper_id": paper.id,
                    "model": model_name,
                    "profile": profile,
                    "themes": themes,
                    "generated_by": user.get("username", "unknown"),
                },
            )
            session.add(draft)
            session.flush()  # get draft.id
            link_draft_to_paper(draft, paper, session)
            if body.force_regen:
                _delete_drafts_for_paper(session, paper.id, except_ids={draft.id})
            paper.article_status = "generated"
            session.commit()
        except Exception as e:
            session.rollback()
            current = session.get(Paper, paper_id)
            if current:
                current.article_status = previous_article_status
                session.commit()
            raise internal_http_error(
                logger, "Save generated paper draft", e,
                detail="新稿保存失败，请稍后重试",
            ) from e

        return {
            "draft_id": draft.id,
            "title": draft.title,
            "paper_id": paper.id,
            "model": model_name,
            "profile": profile,
            "themes": themes,
        }

def poll_generated_video_download(payload: dict) -> dict:
    """Poll the configured video provider and save the generated MP4."""
    from core.config_loader import ConfigLoader
    from tools.storage import get_storage_root

    workspace = Path(payload.get("workspace") or settings.workspace)
    config_dir = Path(payload.get("config_dir") or settings.config_dir)
    if not workspace.is_absolute():
        workspace = (Path(__file__).resolve().parents[4] / workspace).resolve()
    config = ConfigLoader(workspace / config_dir).load_all()
    video_config = (config.get("runtime", {}) or {}).get("video_generation", {}) or {}
    status_url = video_config.get("status_url_template", "")
    token = video_config.get("api_token", "")
    if not status_url or not token:
        raise RuntimeError("视频生成服务未配置")

    task_id = str(payload["task_id"])
    video_id = int(payload["video_id"])
    attempts = int(payload.get("poll_attempts", 60))
    interval = float(payload.get("poll_interval", 10))
    client = requests.Session()
    for attempt in range(attempts):
        time.sleep(interval)
        try:
            response = client.get(
                status_url.replace("{task_id}", urllib.parse.quote(task_id, safe="")),
                headers={"Authorization": f"Bearer {token}"},
                timeout=15,
            )
            data = response.json()
            output = data.get("output", {}) or {}
            status = output.get("task_status") or data.get("status", "")
            video_url = output.get("video_url") or data.get("video_url", "")
            if status == "SUCCEEDED" and video_url:
                video_dir = get_storage_root(str(workspace)) / "videos"
                video_dir.mkdir(parents=True, exist_ok=True)
                local_path = video_dir / f"{task_id}.mp4"
                with safe_get(client, video_url, timeout=120, stream=True) as download:
                    download.raise_for_status()
                    with open(local_path, "wb") as output_file:
                        for chunk in download.iter_content(chunk_size=8192):
                            if chunk:
                                output_file.write(chunk)
                with get_session(str(workspace), str(config_dir)) as session:
                    video = session.get(ChannelVideo, video_id)
                    if video is None:
                        client.close()
                        return {"status": "missing", "video_id": video_id}
                    video.file_path = str(local_path)
                    video.file_size = local_path.stat().st_size
                    video.status = "draft"
                    video.updated_at = utc_now()
                    info = decode_json(video.script, {})
                    if not isinstance(info, dict):
                        info = {}
                    info.update(status="completed", video_url=video_url, local_path=str(local_path))
                    video.script = json.dumps(info, ensure_ascii=False)
                logger.info("[VideoGen] Downloaded video %s", video_id)
                client.close()
                return {"status": "completed", "video_id": video_id, "local_path": str(local_path)}
            if status == "FAILED":
                with get_session(str(workspace), str(config_dir)) as session:
                    video = session.get(ChannelVideo, video_id)
                    if video is not None:
                        video.status = "error"
                        video.updated_at = utc_now()
                client.close()
                return {"status": "failed", "video_id": video_id}
        except Exception as exc:
            logger.warning("[VideoGen] Poll attempt %d failed: %s", attempt + 1, exc)

    client.close()
    with get_session(str(workspace), str(config_dir)) as session:
        video = session.get(ChannelVideo, video_id)
        if video is not None:
            video.status = "error"
            video.updated_at = utc_now()
    logger.error("[VideoGen] Task %s timed out", task_id)
    return {"status": "timeout", "video_id": video_id}

@router.post("/{paper_id}/generate-video")
def generate_video(
    paper_id: int,
    body: GenerateVideoRequest,
    user: dict = Depends(get_current_user),
):
    """
    Two-stage video generation:
      Stage 1: Use 大语言模型 to convert paper text → visual storyboard
      Stage 2: Submit the storyboard to the configured video generation service

    Requires: paper with extracted_text (PDF uploaded), or falls back to abstract.
    """
    workspace = Path(settings.workspace)
    config_dir = settings.config_dir
    with get_session(workspace=settings.workspace, config_dir=config_dir) as session:
        paper = session.get(Paper, paper_id)
        if not paper:
            raise HTTPException(status_code=404, detail="Paper not found")

        # ── Load configured language and video services ───────────
        config = ConfigLoader(workspace / config_dir).load_all()
        domain_config = config.get("domain") or load_domain()
        if not is_usable_for_generation(
            paper,
            generation_requires_target_entity(domain_config),
            get_target_label(),
        ):
            raise HTTPException(status_code=400, detail="论文需完成处理、审核通过且符合生成课题范围后才能生成")
        domain_name = str(domain_config.get("name") or domain_config.get("short_name") or "科研领域")
        domain_intro = str((domain_config.get("prompt") or {}).get("domain_intro") or "")
        default_tags = ",".join(domain_tags(domain_config))
        runtime = config.get("runtime", {})
        provider = runtime.get("provider", {}) or {}
        llm_base_url = provider.get("base_url", "")
        llm_api_key = provider.get("api_key", "")
        model_sequence = config.get("models", {}).get("routing", {}).get("default_auto_sequence", [])
        llm_model = provider.get("model") or (model_sequence[0] if model_sequence else "")
        video_config = runtime.get("video_generation", {}) or {}
        video_api_url = video_config.get("api_url", "")
        video_status_url = video_config.get("status_url_template", "")
        video_token = video_config.get("api_token", "")
        video_model = video_config.get("model", "")
        if not video_api_url or not video_status_url or not video_token or not video_model:
            raise HTTPException(status_code=503, detail="视频生成服务未配置")

        # ── Gather paper content ──────────────────────────────────
        title = (paper.title_zh or paper.title or "")
        title_en = (paper.title or "")
        abstract = (paper.abstract or "")[:2000]
        full_text = (paper.extracted_text or "")[:8000]  # First 8000 chars of PDF text
        equipment = ""
        if paper.equipment_brand:
            if isinstance(paper.equipment_brand, list):
                equipment = ", ".join(str(b) for b in paper.equipment_brand)
            elif isinstance(paper.equipment_brand, str):
                equipment = paper.equipment_brand
        journal = paper.journal_name or ""
        year = str(paper.year or "")
        keywords = (paper.research_fields or [])[:5] if isinstance(paper.research_fields, list) else []

        # Content richness assessment
        has_full_text = len(full_text) > 500
        content_source = "论文全文（含引言、方法、结果）" if has_full_text else "论文摘要"

        # ── Stage 1: LLM → Visual Storyboard ─────────────────────
        style_desc = video_style_prompt(body.style, domain_config)
        storyboard_prompt = f"""你是一个科研论文短视频导演。请根据以下论文信息，生成一个 {body.duration} 秒的视觉分镜脚本。

## 领域背景
{domain_name}：{domain_intro}

## 视频风格
{style_desc}

## 论文信息
- 标题: {title}
- 英文标题: {title_en}
- 期刊: {journal} ({year})
- 关键词: {", ".join(keywords) if keywords else default_tags}
- 使用设备: {equipment or "论文中报告的成像设备"}
- 摘要: {abstract}

## 论文正文（{content_source}）
{full_text if has_full_text else abstract}

## 要求
1. 将 {body.duration} 秒拆分为 3-5 个连续场景，每个场景 1-3 秒
2. 每个场景用一句英文描述画面（生成服务需要英文 prompt），要求具体、可视化、可渲染
3. 描述中应包含：镜头类型（close-up/wide/aerial）、主体、动作、色调、光线
4. 整体画面连贯，有叙事节奏（开端→发展→高潮→结尾）
5. 避免抽象概念（如"神经可塑性"），改用可视化的具体画面（如"神经元之间闪烁的连接"）

请以 JSON 格式输出：
{{
  "title_cn": "视频标题（中文，15字以内）",
  "description_cn": "视频简介（中文，100字以内）",
  "tags_cn": "标签1,标签2,标签3,标签4,标签5",
  "style_description": "整体视觉风格一句话描述（英文）",
  "scenes": [
    {{"duration_sec": 1.5, "prompt_en": "Wide shot of a medical imaging research setting, a participant preparing for a scan, soft neutral lighting, imaging equipment in the background"}},
    ...
  ]
}}"""

        # Call the configured LLM for storyboard generation
        if llm_api_key and llm_base_url:
            try:
                llm_client = OpenAICompatibleClient(base_url=llm_base_url, api_key=llm_api_key)
                llm_response = llm_client.chat(
                    llm_model,
                    [{"role": "user", "content": storyboard_prompt}],
                )
            except Exception as e:
                llm_response = None
                logger.warning(f"[VideoGen] LLM storyboard failed, falling back to abstract: {e}")
        else:
            llm_response = None

        # Parse LLM response or build fallback
        storyboard = None
        if llm_response:
            try:
                # Extract JSON from response
                json_match = re.search(r'\{[^{}]*"scenes"[^{}]*\[.*?\][^{}]*\}', llm_response, re.DOTALL)
                if json_match:
                    storyboard = json.loads(json_match.group())
            except Exception:
                logger.warning('处理失败，已跳过该项（generate_video）', exc_info=True)
                pass

        if not storyboard:
            # Fallback: build simple storyboard from abstract
            storyboard = {
                "title_cn": title[:15],
                "description_cn": abstract[:100],
                "tags_cn": default_tags,
                "style_description": "Scientific visualization with brain scan imagery, blue-white color palette",
                "scenes": [
                    {"duration_sec": 1.0, "prompt_en": f"Title card with text '{title_en[:60]}', academic journal style, dark blue background"},
                    {"duration_sec": 1.5, "prompt_en": f"Scientific visualization of quantitative medical image maps and measured tissue properties, {equipment or 'an imaging system'} in a research setting, neutral tones"},
                    {"duration_sec": 1.5, "prompt_en": "Scientific data visualization, brain activity heatmap pulsating, neural network connections glowing, medical imaging style"},
                    {"duration_sec": 1.0, "prompt_en": "Laboratory setting with research participants, modern neuroscience equipment, professional lighting"},
                ],
            }

        # ── Stage 2: configured video generation service ──────────
        # Assemble final visual prompt from storyboard scenes
        scene_prompts = [s["prompt_en"] for s in storyboard.get("scenes", [])]
        combined_prompt = ". ".join(scene_prompts)
        # Bound prompt length before sending it to the configured service.
        if len(combined_prompt) > 500:
            combined_prompt = combined_prompt[:497] + "..."

        payload = {
            "model": video_model,
            "input": {"prompt": combined_prompt},
            "parameters": {
                "resolution": body.resolution,
                "ratio": body.ratio,
                "duration": body.duration,
            },
        }

        with requests.Session() as video_session:
            resp = video_session.post(
                video_api_url,
                json=payload,
                headers={
                    "Authorization": f"Bearer {video_token}",
                    "Content-Type": "application/json",
                },
                timeout=30,
            )
            if resp.status_code != 200:
                logger.warning("Video generation provider returned HTTP %s", resp.status_code)
                raise HTTPException(status_code=502, detail="视频生成服务暂时不可用，请稍后重试")
            data = resp.json()
        task_id = data.get("output", {}).get("task_id") or data.get("task_id") or data.get("id", "")
        if not task_id:
            raise HTTPException(status_code=502, detail="视频生成服务未返回任务 ID")

        # ── Create video entry ────────────────────────────────────
        with get_session(str(Path.cwd()), "config") as v_session:
            video = ChannelVideo(
                title=storyboard.get("title_cn", title[:50]),
                description=storyboard.get("description_cn", abstract[:200]),
                tags=storyboard.get("tags_cn", default_tags),
                status="generating", source_paper_id=paper.internal_id,
                script=json.dumps({
                    "task_id": task_id,
                    "status": "pending",
                    "storyboard": storyboard,
                    "prompt_used": combined_prompt,
                    "content_source": content_source,
                }, ensure_ascii=False),
                created_at=utc_now(), updated_at=utc_now(),
            )
            v_session.add(video)
            v_session.flush()
            video_id = video.id
            v_session.commit()

        from worker.queue import enqueue
        job_id = enqueue(
            "poll_download",
            {
                "task_id": task_id,
                "video_id": video_id,
                "workspace": settings.workspace,
                "config_dir": settings.config_dir,
            },
            dedupe_key=f"poll-download:{task_id}",
            max_attempts=2,
        )

        return {
            "status": "ok",
            "task_id": task_id,
            "job_id": job_id,
            "video_id": video_id,
            "title": storyboard.get("title_cn", ""),
            "scenes": len(storyboard.get("scenes", [])),
            "content_source": content_source,
            "parameters": {"resolution": body.resolution, "ratio": body.ratio, "duration": body.duration},
        }

@router.get("/video-task/{task_id}")
def poll_video_task(task_id: str, user: dict = Depends(get_current_user)):
    """Poll the configured video service task status."""
    from core.config_loader import ConfigLoader
    cfg = ConfigLoader(Path(settings.workspace) / settings.config_dir).load_all().get("runtime", {})
    video_config = cfg.get("video_generation", {}) or {}
    status_template = video_config.get("status_url_template", "")
    token = video_config.get("api_token", "")
    if not status_template or not token:
        raise HTTPException(status_code=503, detail="视频生成服务未配置")

    with requests.Session() as sess:
        resp = sess.get(
            status_template.replace("{task_id}", urllib.parse.quote(task_id, safe="")),
            headers={"Authorization": f"Bearer {token}"},
            timeout=15,
        )
        data = resp.json()
    output = data.get("output", {}) or {}
    status = output.get("task_status") or data.get("status", "UNKNOWN")
    video_url = output.get("video_url") or data.get("video_url", "")

    from worker.queue import find_by_dedupe_key
    job = find_by_dedupe_key(f"poll-download:{task_id}")
    result = {"task_id": task_id, "status": status, "video_url": video_url}
    if job:
        result.update(job_id=job["id"], job_status=job["status"])
        if job.get("result", {}).get("status"):
            result["download_status"] = job["result"]["status"]
    return result

@router.post("/batch-generate-draft")
def batch_generate_draft(
    body: BatchGenerateDraftRequest,
    user: dict = Depends(get_current_user),
):
    workspace = Path(settings.workspace)
    config_dir = settings.config_dir

    # Load configs once
    try:
        loader = ConfigLoader(workspace / config_dir)
        cfg = loader.load_all()
    except Exception as e:
        raise internal_http_error(
            logger, "Load batch draft configuration", e,
            status_code=503, detail="服务配置暂不可用，请稍后重试",
        ) from e

    runtime = cfg.get("runtime", {})
    models_cfg = cfg.get("models", {})
    prompts_cfg = cfg.get("prompts", {})
    domain_config = cfg.get("domain") or {}
    target_label = get_target_label()

    provider = runtime.get("provider", {})
    base_url = provider.get("base_url", "")
    api_key = provider.get("api_key", "")

    if not base_url or not api_key:
        raise HTTPException(status_code=503, detail="生成服务暂不可用，请检查服务端配置")

    llm = OpenAICompatibleClient(base_url=base_url, api_key=api_key)

    model_name = body.model
    if not model_name or model_name == "auto":
        routing = models_cfg.get("routing", {})
        auto_seq = routing.get("default_auto_sequence", [])
        all_ids_batch = [m["id"] for m in models_cfg.get("models", [])]
        model_name = (
            auto_seq[0]
            if auto_seq
            else (all_ids_batch[0] if all_ids_batch else models_cfg.get("default_model", ""))
        )
    model_cfg_batch = {}
    for m in models_cfg.get("models", []):
        if m["id"] == model_name:
            model_cfg_batch = m
            break
    if not model_cfg_batch:
        raise HTTPException(status_code=503, detail="生成服务暂不可用，请检查服务端配置")

    themes = body.themes or ["临床"]
    profile = body.profile or "default"
    profile_data = loader.load_profile(profile) if profile != "default" else {}

    # Validate papers and collect info
    paper_infos: List[Dict[str, Any]] = []
    with get_session(workspace=settings.workspace, config_dir=config_dir) as session:
        for paper_id in body.paper_ids:
            paper = session.get(Paper, paper_id)
            if not paper:
                continue
            if not is_usable_for_generation(
                paper,
                generation_requires_target_entity(domain_config),
                target_label,
            ):
                continue

            jcr_quartile = ""
            impact_factor = ""
            if paper.journal and paper.journal.metrics:
                latest = sorted(paper.journal.metrics, key=lambda m: m.year or 0, reverse=True)
                if latest:
                    metric = latest[0]
                    impact_factor = str(metric.impact_factor) if metric.impact_factor is not None else ""
                    jcr_quartile = metric.jcr_quartile if metric.jcr_quartile else ""

            rec = {
                "论文编号": str(paper.internal_id or paper.id),
                "API权威标题": paper.title or "",
                "英文标题": paper.title or "",
                "API权威期刊": paper.journal_name or "",
                "期刊名称": paper.journal_name or "",
                "API发表日期": str(paper.year or ""),
                "DOI": paper.doi or "",
                "IF": impact_factor,
                "JCR分区": jcr_quartile,
                "PMID": paper.pmid or "",
                "apa_citation": paper.apa_citation or "",
                "cites_target": paper.cites_target,
                "entity_label": paper.entity_label,
            }
            extracted_text = paper.extracted_text or ""
            image_count = len(paper.extracted_images) if paper.extracted_images and isinstance(paper.extracted_images, list) else 0
            messages = build_messages(
                rec, themes, prompts_cfg, extracted_text, image_count,
                profile=profile, cites_target=paper.cites_target,
                profile_data=profile_data,
            )

            paper_infos.append({
                "paper_id": paper.id,
                "messages": messages,
                "internal_id": paper.internal_id,
                "previous_article_status": paper.article_status,
            })
            # 仅标记生成中，不触碰旧稿；生成失败时会恢复此值。
            paper.article_status = "generating"
        session.commit()

    if not paper_infos:
        return {"success": 0, "failed": 0, "skipped": len(body.paper_ids)}

    success = 0
    failed = 0
    with get_session(workspace=settings.workspace, config_dir=config_dir) as session:
        for info in paper_infos:
            paper = session.get(Paper, info["paper_id"])
            if not paper:
                failed += 1
                continue
            try:
                content = llm.chat(model_name, info["messages"], thinking=model_cfg_batch.get("thinking"))
                chinese_title = _extract_title(content, paper.title or "")

                draft = ArticleDraft(
                    draft_type="single_daily",
                    title=chinese_title,
                    content=content,
                    status="draft",
                    meta={
                        "paper_id": paper.id,
                        "model": model_name,
                        "profile": profile,
                        "themes": themes,
                        "generated_by": user.get("username", "unknown"),
                    },
                )
                session.add(draft)
                session.flush()
                link_draft_to_paper(draft, paper, session)
                if body.force_regen:
                    _delete_drafts_for_paper(session, paper.id, except_ids={draft.id})
                paper.article_status = "generated"
                session.commit()
                success += 1
            except Exception:
                session.rollback()
                current = session.get(Paper, info["paper_id"])
                if current:
                    current.article_status = info["previous_article_status"]
                    session.commit()
                failed += 1

    return {"success": success, "failed": failed, "skipped": len(body.paper_ids) - success - failed}

@router.post("/batch-review")
def batch_review(
    body: BatchReviewRequest,
    user: dict = Depends(get_current_user),
):
    """选中多篇论文，生成一篇综述或合集推文（profile-aware）。"""
    workspace = Path(settings.workspace)
    config_dir = settings.config_dir

    # Load configs
    config_loader = ConfigLoader(workspace / config_dir)
    all_cfg = config_loader.load_all()
    prompts_cfg = all_cfg.get("prompts", {})
    models_cfg = all_cfg.get("models", {})
    domain_config = all_cfg.get("domain") or load_domain()
    target_label = get_target_label()
    domain_name = str(domain_config.get("name") or domain_config.get("short_name") or "科研领域")
    domain_intro = str((domain_config.get("prompt") or {}).get("domain_intro") or "")

    # Init LLM
    runtime_cfg = all_cfg.get("runtime", {})
    provider = runtime_cfg.get("provider", {})
    llm = OpenAICompatibleClient(
        base_url=provider.get("base_url", ""),
        api_key=provider.get("api_key", ""),
        timeout_seconds=provider.get("timeout", 300),
    )

    model_name = body.model
    if not model_name or model_name == "auto":
        routing = models_cfg.get("routing", {})
        auto_seq = routing.get("default_auto_sequence", [])
        all_ids_rev = [m["id"] for m in models_cfg.get("models", [])]
        model_name = (
            auto_seq[0]
            if auto_seq
            else (all_ids_rev[0] if all_ids_rev else models_cfg.get("default_model", ""))
        )
    model_cfg_rev = {}
    for m in models_cfg.get("models", []):
        if m["id"] == model_name:
            model_cfg_rev = m
            break

    themes = body.themes or ["临床"]
    theme_str = ", ".join(themes)
    profile = body.profile or "default"
    profile_data = config_loader.load_profile(profile) if profile != "default" else {}

    # Collect paper summaries
    paper_summaries: List[Dict[str, Any]] = []
    with get_session(workspace=settings.workspace, config_dir=config_dir) as session:
        for paper_id in body.paper_ids:
            paper = session.get(Paper, paper_id)
            if not paper:
                continue
            if not is_usable_for_generation(
                paper,
                generation_requires_target_entity(domain_config),
                target_label,
            ):
                continue
            # Use abstract, conclusions_zh, highlights_zh, or extracted_text as fallback
            summary_text = ""
            if paper.abstract:
                summary_text = paper.abstract
            elif paper.conclusions_zh:
                summary_text = paper.conclusions_zh
            elif paper.highlights_zh and isinstance(paper.highlights_zh, list):
                summary_text = "\n".join(str(h) for h in paper.highlights_zh)
            elif paper.extracted_text:
                # Truncate to first 1500 chars if no structured summary
                summary_text = paper.extracted_text[:1500]

            # Device & team info from DB
            device_info = ""
            entity_matches_target = bool(target_label and paper.entity_label == target_label)
            if paper.cites_target and entity_matches_target:
                device_info = "论文资料确认提及目标课题"
                if paper.device_citation_text:
                    device_info += f"; 引用: {paper.device_citation_text[:200]}"
            ordered = _get_ordered_authors(session, paper.id) if session else []
            ca_list = [a for a in ordered if a.get("is_corresponding")]
            fa_list = [a for a in ordered if a.get("is_first")]
            team_info = ""
            if ca_list:
                ca = ca_list[0]
                team_info = f"通讯作者: {ca.get('name', '')}"
                if ca.get('affiliation'):
                    team_info += f" ({ca.get('affiliation', '')})"

            paper_summaries.append({
                "id": paper.id,
                "internal_id": paper.internal_id,
                "title": paper.title or "",
                "year": paper.year,
                "journal": paper.journal_name or "",
                "doi": paper.doi or "",
                "authors": [a.get("name", "") for a in fa_list if a.get("name")],
                "summary": summary_text,
                "image_count": len(paper.extracted_images) if paper.extracted_images and isinstance(paper.extracted_images, list) else 0,
                "device_info": device_info,
                "cites_target": paper.cites_target,
                "entity_label": paper.entity_label,
                "team_info": team_info,
                "citation": paper.apa_citation or "",
            })

    if len(paper_summaries) < 2:
        raise HTTPException(status_code=400, detail="至少需要选择 2 篇论文生成综述")

    # 标记论文为生成中（持久化状态，LLM 调用前提交）
    with get_session(workspace=settings.workspace, config_dir=config_dir) as session:
        for ps in paper_summaries:
            paper = session.get(Paper, ps["id"])
            if paper:
                paper.article_status = "generating"
        session.commit()

    # Build collection prompt — profile-aware
    system_prompt = (
        profile_data.get("review_system_prompt")
        or prompts_cfg.get("system_prompt", "你是科研编辑。")
    )
    structure = profile_data.get("review_structure") or []

    # Build paper summaries block (inject device & team info + citation from DB)
    summaries_block = "\n\n".join([
        f"【论文 {i+1}】\n标题：{p['title']}\n"
        + (f"团队：{p['team_info']}\n" if p.get('team_info') else "")
        + (f"设备：{p['device_info']}\n" if p.get('device_info') else "")
        + f"期刊：{p['journal']} ({p['year']})\nDOI：{p['doi']}\n"
        + f"预构建参考文献：{p['citation']}\n"
        + f"核心内容：\n{p['summary'][:800]}"
        for i, p in enumerate(paper_summaries)
    ])

    # Add a neutral, optional note only when a configured target is cited.
    target_label = get_target_label()
    has_target_reference = target_label and any(
        paper.get("cites_target") and paper.get("entity_label") == target_label
        for paper in paper_summaries
    )
    target_reference_note = ""
    if profile == "collection" and has_target_reference:
        target_reference_note = (
            f"\n\n# 可选课题说明\n论文证据确认提及 {target_label}。"
            "仅在内容相关处客观说明其在研究中的作用，不延伸为宣传内容。"
        )

    # Image hints block (collection profile only)
    images_hint = ""
    if profile == "collection":
        image_lines = []
        for i, p in enumerate(paper_summaries):
            if p.get("image_count", 0) > 0:
                image_lines.append(f"- 论文 {i+1}「{p['title'][:50]}」有 {p['image_count']} 张可用图片")
        if image_lines:
            images_hint = (
                "\n\n# 可用论文图片\n"
                + "\n".join(image_lines)
                + "\n\n在「四、论文详解」中适当位置使用 `[插入原图：具体描述]` 标记指示配图。"
            )

    # Theme guidance
    theme_templates = profile_data.get("theme_templates") or prompts_cfg.get("theme_templates", {})
    theme_guidance_lines = []
    for t in themes:
        template = theme_templates.get(t, f"围绕「{t}」主题进行深入解读。")
        theme_guidance_lines.append(f"### 主题「{t}」的解读要求：\n{template.strip()}")

    structure_block = "\n".join([
        (f"{item['section']}：" if item.get('section') else "") + f"{item['guidance']}"
        + (f"（{item['char_range']}）" if item.get('char_range') else "")
        for item in structure
    ])

    if profile == "collection":
        requirements_lines = [
            "1. 不要简单罗列每篇论文，要进行横向对比和整合分析",
            "2. 找出各篇研究的共性发现和方法创新",
            "3. 说明设备与软件选择对研究设计和结果解释的影响",
            "4. 避免罗列与分析无关的产品参数",
            "5. 所有数据必须忠实于原文，不得虚构",
            "6. 输出 Markdown 格式，使用 `##` 作为章节标题",
        ]
    else:
        requirements_lines = [
            "1. 不要简单罗列每篇论文，要进行横向对比和整合分析",
            "2. 找出各篇研究的共性发现和方法创新",
            "3. 指出领域趋势和未来方向",
            "4. 所有数据必须忠实于原文，不得虚构",
            "5. 输出 Markdown 格式，使用 `##` 作为章节标题",
        ]

    user_prompt = (
        f"请基于以下 {len(paper_summaries)} 篇{domain_name}相关学术论文，撰写一篇中文合集推文。\n"
        f"领域背景：{domain_intro}\n\n"
        f"主题侧重：{theme_str}\n\n"
        + ("\n".join(theme_guidance_lines) + "\n\n" if theme_guidance_lines else "") +
        f"=== 论文摘要 ===\n{summaries_block}"
        f"{target_reference_note}"
        f"{images_hint}\n\n"
        f"=== 文章结构要求 ===\n{structure_block}\n\n"
        f"要求：\n" +
        "\n".join(requirements_lines)
    )

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]

    try:
        content = llm.chat(model_name, messages, thinking=model_cfg_rev.get("thinking"))
        chinese_title = _extract_title(content, f"{domain_name}研究进展合集")

        with get_session(workspace=settings.workspace, config_dir=config_dir) as session:
            draft_type = "collection" if profile == "collection" else "review"
            draft = ArticleDraft(
                draft_type=draft_type,
                title=chinese_title,
                content=content,
                status="draft",
                meta={
                    "paper_ids": [p["id"] for p in paper_summaries],
                    "model": model_name,
                    "profile": body.profile,
                    "themes": themes,
                    "generated_by": user.get("username", "unknown"),
                },
            )
            session.add(draft)
            session.flush()
            # Link all papers and mark them as generated
            for p_summary in paper_summaries:
                paper = session.get(Paper, p_summary["id"])
                if paper:
                    link_draft_to_paper(draft, paper, session)
                    paper.article_status = "generated"
            session.commit()
            session.commit()
            draft_id = draft.id

        return {
            "success": True,
            "draft_id": draft_id,
            "title": chinese_title,
            "paper_count": len(paper_summaries),
        }
    except Exception:
        logger.exception("Batch review generation failed")
        return {"success": False, "error": "综述生成失败，请稍后重试"}

@router.post("/summary-jobs", status_code=202)
def create_word_summary_job(
    body: BatchGenerateWordSummaryRequest,
    user: dict = Depends(get_current_user),
):
    """Create or reuse a persistent Word-summary task."""
    if not 1 <= len(body.paper_ids) <= 100:
        raise HTTPException(status_code=400, detail="论文总结单次需要选择 1-100 篇论文")
    from ...summary_jobs import create_summary_job
    from ...summary_service import resolve_summary_model

    return create_summary_job(
        body.paper_ids,
        profile=body.profile or "report",
        model=resolve_summary_model(body.model),
        actor=user,
    )

@router.get("/summary-jobs/{job_id}")
def read_word_summary_job(
    job_id: str,
    user: dict = Depends(get_current_user),
):
    """Read progress or the durable result of a Word-summary task."""
    from ...summary_jobs import get_summary_job

    return get_summary_job(job_id, user)

@router.post("/summary-jobs/{job_id}/rerender")
def rerender_word_summary_job(
    job_id: str,
    user: dict = Depends(get_current_user),
):
    """Regenerate only the Word file from a persisted successful report."""
    from ...summary_jobs import rerender_summary_job

    return rerender_summary_job(job_id, user)

def _validate_word_report_data(report_data: Any, paper_count: int = 0) -> List[str]:
    """Reject syntactically valid but incomplete LLM report objects."""
    if not isinstance(report_data, dict):
        return ["顶层不是 JSON 对象"]

    errors: List[str] = []
    if not str(report_data.get("report_title") or "").strip():
        errors.append("缺少报告标题")
    if len(str(report_data.get("overview") or "").strip()) < 50:
        errors.append("概述过短")

    sections = report_data.get("sections")
    if not isinstance(sections, list) or len(sections) < 7:
        errors.append(f"章节数量不足（实际 {len(sections) if isinstance(sections, list) else 0}，至少 7）")
        return errors

    headings = "\n".join(
        str(section.get("heading") or "")
        for section in sections
        if isinstance(section, dict)
    )
    for marker in ("一、", "论文基本信息", "二、", "三、", "四、", "五、", "六、"):
        if marker not in headings:
            errors.append(f"缺少章节：{marker}")

    table_count = sum(
        1
        for section in sections
        if isinstance(section, dict) and isinstance(section.get("table"), dict)
    )
    if table_count < 2:
        errors.append(f"表格数量不足（实际 {table_count}，至少 2）")
    return errors

@router.post("/batch-generate-word-summary")
def batch_generate_word_summary(
    body: BatchGenerateWordSummaryRequest,
    request: Request,
    user: dict = Depends(get_current_user),
):
    """选中 1-100 篇论文，生成一份 Word 格式的总结报告并返回下载链接。

    - 1 篇：单篇深度解析（类似 detail 推文，但输出为 Word 报告格式）
    - 2-9 篇：小样本综合报告
    - 10+ 篇：完整综合报告
    """
    if len(body.paper_ids) < 1:
        raise HTTPException(status_code=400, detail="至少需要选择 1 篇论文生成总结")
    if len(body.paper_ids) > 100:
        raise HTTPException(status_code=400, detail="单次最多支持 100 篇论文")

    # Backward-compatible synchronous endpoint. It now uses the same fixed
    # report skeleton and same-model repair pipeline as persistent jobs.
    from ...summary_service import generate_summary_artifact
    try:
        result, _report, _repair_count = generate_summary_artifact(
            body.paper_ids,
            profile=body.profile or "report",
            model=body.model,
            actor=user,
            request=request,
        )
        return result
    except HTTPException:
        raise
    except Exception as exc:
        raise internal_http_error(
            logger, "Generate Word summary", exc,
            detail="论文总结生成失败，请稍后重试",
        ) from exc
