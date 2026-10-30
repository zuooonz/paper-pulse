"""Papers API package."""

from fastapi import APIRouter

from . import common as common
from . import detail, exports, generation, ingest, listing
from . import schemas as schemas

from .common import (
    COUNTRY_EN_TO_ZH,
    COUNTRY_ZH_TO_EN,
    PAPER_FILTER_PROCESSING_STATUSES,
    PAPER_FILTER_REVIEW_OUTCOMES,
    _PAPER_AUTHOR_ROLE_FLAGS,
    _apply_sort,
    _build_base_query,
    _current_taxonomy,
    _database_change_token,
    _extract_authors_from_doi_meta,
    _extract_authors_from_original_data,
    _extract_title,
    _get_cached_filter_options,
    _get_cached_paper_list,
    _get_ordered_authors,
    _get_ordered_authors_for_papers,
    _impact_factor_subquery,
    _latest_metric_subquery,
    _paper_to_dict,
    _parse_ids,
    _set_cached_filter_options,
    _set_cached_paper_list,
    _taxonomy_filter_values,
    _taxonomy_values,
    logger,
)

from .listing import (
    get_filter_options,
    list_entities,
    list_papers,
)

from .exports import (
    batch_download_pdf,
    export_papers,
    get_citation,
)

from .common import _get_jcr_matcher

from .ingest import (
    _DOI_ENTITY_TEXT_FIELDS,
    _doi_import_entity_fields,
    _doi_import_mark_as_target,
    _doi_metadata_text,
    batch_doi_upload,
    batch_pdf_upload,
    download_pdf,
    get_pdf,
    upload_pdf,
)

from .generation import (
    _delete_drafts_for_paper,
    _validate_word_report_data,
    batch_generate_draft,
    batch_generate_word_summary,
    batch_review,
    create_word_summary_job,
    generate_draft,
    generate_video,
    poll_generated_video_download,
    poll_video_task,
    read_word_summary_job,
    rerender_word_summary_job,
)

from .detail import (
    _build_apa_citation,
    _delete_orphan_authors,
    _delete_paper_files,
    _stage_paper_deletion,
    _sync_full_author_list,
    _to_apa_author,
    _upsert_author_from_form,
    _upsert_paper_author_link,
    batch_delete_papers,
    delete_paper,
    get_paper,
    review_paper,
    update_paper,
    update_paper_doi,
)

from .schemas import (
    BatchDeleteBody,
    BatchDoiRequest,
    BatchDoiResultItem,
    BatchDownloadBody,
    BatchGenerateDraftRequest,
    BatchGenerateWordSummaryRequest,
    BatchReviewRequest,
    DeletePaperBody,
    DeleteReasonBody,
    GenerateDraftRequest,
    GenerateVideoRequest,
    MultimodalRelationRequest,
    ReviewRequest,
    UpdateDoiRequest,
    UpdatePaperRequest,
)

from ...config import settings as settings
from ...dependencies import get_current_user as get_current_user
from ...dependencies import require_reviewer as require_reviewer
from ...dependencies import require_uploader as require_uploader
from ...dependencies import require_paper_editor as require_paper_editor
from paper_library.db import get_session
from core.config_loader import ConfigLoader as ConfigLoader
from core.config_loader import load_yaml_config as load_yaml_config
from core.llm_client import OpenAICompatibleClient as OpenAICompatibleClient
from core.article_builder import build_messages as build_messages
from core.entity_detector import get_target_label as get_target_label

__all__ = [
    'BatchDeleteBody',
    'BatchDoiRequest',
    'BatchDoiResultItem',
    'BatchDownloadBody',
    'BatchGenerateDraftRequest',
    'BatchGenerateWordSummaryRequest',
    'BatchReviewRequest',
    'COUNTRY_EN_TO_ZH',
    'COUNTRY_ZH_TO_EN',
    'ConfigLoader',
    'DeletePaperBody',
    'DeleteReasonBody',
    'GenerateDraftRequest',
    'GenerateVideoRequest',
    'MultimodalRelationRequest',
    'OpenAICompatibleClient',
    'PAPER_FILTER_PROCESSING_STATUSES',
    'PAPER_FILTER_REVIEW_OUTCOMES',
    'ReviewRequest',
    'UpdateDoiRequest',
    'UpdatePaperRequest',
    '_DOI_ENTITY_TEXT_FIELDS',
    '_PAPER_AUTHOR_ROLE_FLAGS',
    '_apply_sort',
    '_build_apa_citation',
    '_build_base_query',
    '_current_taxonomy',
    '_database_change_token',
    '_delete_drafts_for_paper',
    '_delete_orphan_authors',
    '_delete_paper_files',
    '_doi_import_entity_fields',
    '_doi_import_mark_as_target',
    '_doi_metadata_text',
    '_extract_authors_from_doi_meta',
    '_extract_authors_from_original_data',
    '_extract_title',
    '_get_cached_filter_options',
    '_get_cached_paper_list',
    '_get_jcr_matcher',
    '_get_ordered_authors',
    '_get_ordered_authors_for_papers',
    '_impact_factor_subquery',
    '_latest_metric_subquery',
    '_paper_to_dict',
    '_parse_ids',
    '_set_cached_filter_options',
    '_set_cached_paper_list',
    '_stage_paper_deletion',
    '_sync_full_author_list',
    '_taxonomy_filter_values',
    '_taxonomy_values',
    '_to_apa_author',
    '_upsert_author_from_form',
    '_upsert_paper_author_link',
    '_validate_word_report_data',
    'batch_delete_papers',
    'batch_doi_upload',
    'batch_download_pdf',
    'batch_generate_draft',
    'batch_generate_word_summary',
    'batch_pdf_upload',
    'batch_review',
    'build_messages',
    'create_word_summary_job',
    'delete_paper',
    'download_pdf',
    'export_papers',
    'generate_draft',
    'generate_video',
    'get_citation',
    'get_current_user',
    'get_filter_options',
    'get_paper',
    'get_pdf',
    'get_session',
    'get_target_label',
    'list_entities',
    'list_papers',
    'load_yaml_config',
    'logger',
    'poll_generated_video_download',
    'poll_video_task',
    'read_word_summary_job',
    'require_paper_editor',
    'require_reviewer',
    'require_uploader',
    'rerender_word_summary_job',
    'review_paper',
    'settings',
    'update_paper',
    'update_paper_doi',
    'upload_pdf',
]

router = APIRouter(prefix="/api/papers", tags=["papers"])
for _subrouter in (listing.router, exports.router, ingest.router, generation.router, detail.router):
    router.include_router(_subrouter)
router.add_api_route("", listing.list_papers, methods=["GET"])
