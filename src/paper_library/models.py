"""SQLAlchemy ORM 模型定义"""
import datetime
from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Table,
    Text,
    UniqueConstraint,
    false,
    text,
)
from sqlalchemy.types import JSON
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import relationship
from sqlalchemy.orm import declarative_base

Base = declarative_base()
# SQLite keeps SQLAlchemy's JSON representation; PostgreSQL stores native JSONB.
JSON_TYPE = JSON().with_variant(JSONB(), "postgresql")
# Compatibility aliases: all models now share one SQLAlchemy metadata.
AccountBase = Base
DraftBase = Base


# 论文-作者关联表
paper_authors = Table(
    "paper_authors",
    Base.metadata,
    Column("paper_id", Integer, ForeignKey("papers.id"), primary_key=True),
    Column("author_id", Integer, ForeignKey("authors.id"), primary_key=True),
    Column("author_order", Integer, default=0, nullable=False),
    Column("is_first", Boolean, default=False, nullable=False),
    Column("is_corresponding", Boolean, default=False, nullable=False),
)


# 推文草稿-论文关联表
draft_papers = Table(
    "draft_papers",
    Base.metadata,
    Column("draft_id", Integer, ForeignKey("article_drafts.id", ondelete="CASCADE"), primary_key=True),
    Column("paper_id", Integer, ForeignKey("papers.id", ondelete="CASCADE"), primary_key=True),
)


class Journal(Base):
    """期刊表"""
    __tablename__ = "journals"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(500), nullable=False, index=True)
    name_abbr = Column(String(200), nullable=True)
    issn = Column(String(20), nullable=True)
    eissn = Column(String(20), nullable=True)
    publisher = Column(String(200), nullable=True)
    country = Column(String(100), nullable=True)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.datetime.now(datetime.UTC))
    updated_at = Column(DateTime(timezone=True), default=lambda: datetime.datetime.now(datetime.UTC), onupdate=lambda: datetime.datetime.now(datetime.UTC))

    # 关联
    metrics = relationship("JournalMetric", back_populates="journal")
    papers = relationship("Paper", back_populates="journal")


class JournalMetric(Base):
    """期刊计量指标表（每年）"""
    __tablename__ = "journal_metrics"

    id = Column(Integer, primary_key=True, autoincrement=True)
    journal_id = Column(Integer, ForeignKey("journals.id"), nullable=False)
    year = Column(Integer, nullable=False)
    impact_factor = Column(Float, nullable=True)
    jcr_quartile = Column(String(10), nullable=True)  # Q1/Q2/Q3/Q4
    cas_quartile = Column(String(10), nullable=True)      # 中科院分区
    emerging_quartile = Column(String(10), nullable=True) # 新锐分区
    total_cites = Column(Integer, nullable=True)
    articles = Column(Integer, nullable=True)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.datetime.now(datetime.UTC))
    updated_at = Column(DateTime(timezone=True), default=lambda: datetime.datetime.now(datetime.UTC), onupdate=lambda: datetime.datetime.now(datetime.UTC))

    # 关联
    journal = relationship("Journal", back_populates="metrics")


class Paper(Base):
    """论文表"""
    __tablename__ = "papers"
    __table_args__ = (
        Index("ix_papers_is_in_domain", "is_in_domain"),
        Index("ix_papers_year", "year"),
        Index("ix_papers_entity_label", "entity_label"),
        Index("ix_papers_article_status", "article_status"),
        Index("ix_papers_processing_status", "processing_status"),
        Index("ix_papers_review_outcome", "review_outcome"),
        Index("ix_papers_has_pdf", "has_pdf"),
        Index("ix_papers_metadata_complete", "metadata_complete"),
        Index("ix_papers_corresponding_country", "corresponding_country"),
        Index("ix_papers_corresponding_province", "corresponding_province"),
        Index("ix_papers_corresponding_city", "corresponding_city"),
        Index("ix_papers_corresponding_institution_type", "corresponding_institution_type"),
        Index("ix_papers_subject_disease", "subject_disease"),
        Index("ix_papers_study_design", "study_design"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    # 核心标识
    internal_id = Column(Integer, nullable=True, unique=True, comment="原始 Excel 论文编号")
    doi = Column(String(255), nullable=True, unique=True)
    pmid = Column(String(20), nullable=True)
    title = Column(Text, nullable=False)
    title_zh = Column(Text, nullable=True)
    abstract = Column(Text, nullable=True)
    abstract_zh = Column(Text, nullable=True)

    # 期刊关联
    journal_id = Column(Integer, ForeignKey("journals.id"), nullable=True)
    journal_name = Column(String(500), nullable=True)  # 冗余存储，用于显示
    year = Column(Integer, nullable=True)
    volume = Column(String(50), nullable=True)
    issue = Column(String(50), nullable=True)
    pages = Column(String(50), nullable=True)

    # 领域相关标记
    doi_meta = Column(JSON_TYPE, nullable=True)  # CrossRef 元数据
    is_in_domain = Column(Boolean, nullable=True)
    cites_target = Column(Boolean, default=False, nullable=False)
    entity_label = Column(String(100), nullable=True, comment="论文关联实体显示名")
    entity_source = Column(String(20), nullable=True)  # R3: fulltext_keyword/manual/excel/crossref
    entity_confidence = Column(String(10), nullable=True)  # R3: high/medium/low/ambiguous
    apa_citation = Column(Text, nullable=True)            # 预计算 APA 7th 引用
    device_citation_text = Column(Text, nullable=True)   # 设备引用原句
    citation_error_type = Column(String(100), nullable=True)  # 引用错误类型
    uses_target_software = Column(Boolean, nullable=True)       # 是否使用目标实体的软件
    target_reference_ids = Column(JSON_TYPE, nullable=True)  # 引用的目标论文 ID 列表

    # PDF 存储
    pdf_storage_path = Column(String(1000), nullable=True)  # PDF 文件路径
    pdf_text_extracted = Column(Boolean, default=False)
    extracted_text = Column(Text, nullable=True)
    extracted_images = Column(JSON_TYPE, nullable=True)  # 图片信息数组
    selected_images = Column(JSON_TYPE, nullable=True)  # 用户选中的图片路径列表

    # 论文处理、审核与资料状态相互独立
    processing_status = Column(String(20), default="pending", server_default="pending", nullable=False)
    processing_error = Column(Text, nullable=True)
    review_outcome = Column(String(20), default="unreviewed", server_default="unreviewed", nullable=False)
    reviewed_by = Column(String(100), nullable=True)
    reviewed_at = Column(DateTime(timezone=True), nullable=True)
    review_note = Column(Text, nullable=True)
    has_pdf = Column(Boolean, default=False, server_default=false(), nullable=False)
    metadata_complete = Column(Boolean, default=False, server_default=false(), nullable=False)
    article_status = Column(String(50), default="not_generated", nullable=False)
    # not_generated -> generating -> generated / error

    # 结构化增强字段
    keywords = Column(JSON_TYPE, nullable=True)  # 关键词列表
    keywords_zh = Column(JSON_TYPE, nullable=True)
    research_fields = Column(JSON_TYPE, nullable=True)  # 研究领域分类
    population = Column(JSON_TYPE, nullable=True)  # 研究人群
    paradigm = Column(JSON_TYPE, nullable=True)  # 实验范式
    equipment_brand = Column(JSON_TYPE, nullable=True)  # 设备品牌
    channel_count = Column(Integer, nullable=True)   # 通道数
    sample_size = Column(Integer, nullable=True)
    tags = Column(JSON_TYPE, nullable=True)  # 分类标签，受控项来自 taxonomy.yaml
    conclusions_zh = Column(Text, nullable=True)
    highlights_zh = Column(JSON_TYPE, nullable=True)  # 要点列表
    metadata_confidence = Column(JSON_TYPE, nullable=True)  # 各字段置信度
    brain_regions = Column(JSON_TYPE, nullable=True)  # 研究脑区列表
    multimodal_techniques = Column(JSON_TYPE, nullable=True)  # 联用技术: {has_multimodal, techniques: [{name, category, source}]}
    subject_disease = Column(String(200), nullable=True)  # 确定性归一化后的主要被试疾病/人群
    subject_diseases = Column(JSON_TYPE, nullable=True)  # 多值疾病/人群主题；保留共病、并发症和健康对照
    research_topics = Column(JSON_TYPE, nullable=True)  # 规范化研究主题；与通讯作者科室/院系分离
    neuroscience_subtopics = Column(JSON_TYPE, nullable=True)  # 兼容旧库的补充分类字段
    paradigms = Column(JSON_TYPE, nullable=True)  # 规范化实验范式；保留旧 paradigm 原始值
    uses_multimodal = Column(Boolean, nullable=True)  # 是否实际联用其他技术
    multimodal_methods = Column(JSON_TYPE, nullable=True)  # 规范化联用技术；保留旧复杂设备结构
    multimodal_relations = Column(JSON_TYPE, nullable=True)  # 联用技术与时序关系: [{method, timing}]
    classification_sources = Column(JSON_TYPE, nullable=True)  # 各分类维度的 auto/manual 来源与证据
    subject_age_mean = Column(Float, nullable=True)  # 摘要明确报告的平均年龄
    study_design = Column(String(50), nullable=True)  # 标题/摘要明确声明的主研究设计

    # 作者信息（来自 Excel 手工录入）
    pubmed_url = Column(String(500), nullable=True)
    corresponding_region = Column(String(200), nullable=True)   # 通讯所在地区
    corresponding_country = Column(String(100), nullable=True)  # R1: 通讯作者单位国家
    corresponding_country_source = Column(String(20), nullable=True)  # R1: crossref/pdf/manual
    corresponding_province = Column(String(50), nullable=True)  # 通讯作者主单位省份
    corresponding_city = Column(String(50), nullable=True)  # 通讯作者主单位城市
    corresponding_institution_type = Column(String(50), nullable=True)  # 临床/科研
    corresponding_department = Column(String(200), nullable=True)  # 通讯作者主单位科室/学科
    
    
    # 接受日期（三路提取 + 互相印证）
    accepted_date = Column(Date, nullable=True)
    accepted_date_source = Column(String(20), nullable=True)

    # 发表日期（三路提取 + 互相印证）
    published_date = Column(Date, nullable=True)
    published_date_source = Column(String(20), nullable=True)
    publication_month = Column(Integer, nullable=True)  # R2: 发表月份 1-12
    publication_month_source = Column(String(30), nullable=True)  # R2: 来源+精度

    # 备注
    notes = Column(Text, nullable=True)

    # 来源信息
    source = Column(String(100), nullable=True, comment="数据来源: excel_import, crossref, manual")
    source_excel = Column(String(255), nullable=True)  # 来源 Excel 文件名
    source_row = Column(Integer, nullable=True)  # 来源 Excel 行号
    original_data = Column(JSON_TYPE, nullable=True)  # 原始数据（JSON 存储）

    # 时间戳
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.datetime.now(datetime.UTC))
    updated_at = Column(DateTime(timezone=True), default=lambda: datetime.datetime.now(datetime.UTC), onupdate=lambda: datetime.datetime.now(datetime.UTC))

    # 关联
    journal = relationship("Journal", back_populates="papers")
    authors = relationship("Author", secondary=paper_authors, back_populates="papers")


class AffiliationLocation(Base):
    """Canonical affiliation used by author geography and map queries."""

    __tablename__ = "affiliation_locations"

    id = Column(Integer, primary_key=True, autoincrement=True)
    affiliation_zh = Column(Text, nullable=False, unique=True)
    canonical_name_en = Column(Text, nullable=True)
    city = Column(Text, nullable=True)
    province = Column(Text, nullable=True)
    country = Column(Text, nullable=True)
    lat = Column(Float, nullable=True)
    lng = Column(Float, nullable=True)
    geocoded_at = Column(DateTime(timezone=True), nullable=True)


class Author(Base):
    """作者表"""
    __tablename__ = "authors"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(200), nullable=False, index=True)
    name_zh = Column(String(200), nullable=True)
    orcid = Column(String(100), nullable=True, unique=True)
    email = Column(String(200), nullable=True)
    affiliation = Column(Text, nullable=True)  # 单位
    affiliation_zh = Column(Text, nullable=True)
    country = Column(String(100), nullable=True)
    province = Column(String(50), nullable=True)
    city = Column(String(50), nullable=True)
    canonical_affiliation_id = Column(
        Integer,
        ForeignKey(
            "affiliation_locations.id",
            ondelete="SET NULL",
            name="fk_authors_canonical_affiliation_id_affiliation_locations",
        ),
        nullable=True,
        comment="FK → affiliation_locations.id",
    )
    institution_type = Column(String(50), nullable=True, comment="从人工核对的中文单位确定性提取")
    department = Column(String(200), nullable=True, comment="从人工核对的中文单位确定性提取")
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.datetime.now(datetime.UTC))
    updated_at = Column(DateTime(timezone=True), default=lambda: datetime.datetime.now(datetime.UTC), onupdate=lambda: datetime.datetime.now(datetime.UTC))

    # 关联
    papers = relationship("Paper", secondary=paper_authors, back_populates="authors")


class PaperAuthorMention(Base):
    """Paper-specific author snapshot backed by source evidence.

    ``authors`` represents a person identity.  Names, affiliations and contact
    details printed on one publication belong here so that editing one paper
    cannot silently overwrite every other paper linked to the same person.
    """

    __tablename__ = "paper_author_mentions"
    __table_args__ = (
        UniqueConstraint("paper_id", "author_id", name="uq_paper_author_mentions_link"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    paper_id = Column(Integer, ForeignKey("papers.id"), nullable=False, index=True)
    author_id = Column(Integer, ForeignKey("authors.id"), nullable=False, index=True)
    name_raw = Column(String(200), nullable=False)
    name_zh = Column(String(200), nullable=True)
    affiliation_raw = Column(Text, nullable=True)
    affiliation_zh = Column(Text, nullable=True)
    email_raw = Column(String(320), nullable=True)
    orcid_raw = Column(String(100), nullable=True)
    source_type = Column(String(30), nullable=False)
    source_ref = Column(String(500), nullable=True)
    verification_status = Column(String(20), nullable=False, default="unverified")
    evidence_text = Column(Text, nullable=True)
    verified_by = Column(String(100), nullable=True)
    verified_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.datetime.now(datetime.UTC))
    updated_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.datetime.now(datetime.UTC),
        onupdate=lambda: datetime.datetime.now(datetime.UTC),
    )


class AuthorIdentifier(Base):
    """Traceable identity evidence such as ORCID, email or OpenAlex ID."""

    __tablename__ = "author_identifiers"
    __table_args__ = (
        UniqueConstraint(
            "author_id", "identifier_type", "normalized_value",
            name="uq_author_identifier_value",
        ),
        Index(
            "uq_author_identifier_orcid",
            "identifier_type",
            "normalized_value",
            unique=True,
            sqlite_where=text("identifier_type = 'orcid'"),
            postgresql_where=text("identifier_type = 'orcid'"),
        ),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    author_id = Column(Integer, ForeignKey("authors.id"), nullable=False, index=True)
    identifier_type = Column(String(30), nullable=False, index=True)
    identifier_value = Column(String(500), nullable=False)
    normalized_value = Column(String(500), nullable=False, index=True)
    source_type = Column(String(30), nullable=False)
    source_paper_id = Column(Integer, ForeignKey("papers.id"), nullable=True)
    verification_status = Column(String(20), nullable=False, default="unverified")
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.datetime.now(datetime.UTC))
    verified_at = Column(DateTime(timezone=True), nullable=True)


class AuthorIdentityEvent(Base):
    """Append-only audit trail for author split, merge and relink actions."""

    __tablename__ = "author_identity_events"

    id = Column(Integer, primary_key=True, autoincrement=True)
    event_type = Column(String(30), nullable=False, index=True)
    source_author_id = Column(Integer, nullable=True, index=True)
    target_author_id = Column(Integer, nullable=True, index=True)
    paper_id = Column(Integer, nullable=True, index=True)
    reason = Column(String(500), nullable=False)
    evidence = Column(Text, nullable=True)
    actor = Column(String(100), nullable=True)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.datetime.now(datetime.UTC))


class AffiliationGeoEvidence(Base):
    """Provider response used to audit one canonical affiliation location."""

    __tablename__ = "affiliation_geo_evidence"
    __table_args__ = (
        UniqueConstraint(
            "affiliation_location_id", "provider", "query_text",
            name="uq_affiliation_geo_evidence_query",
        ),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    # Keep evidence independently when a canonical location is removed.
    affiliation_location_id = Column(Integer, nullable=False, index=True)
    provider = Column(String(30), nullable=False, index=True)
    query_text = Column(String(500), nullable=False)
    institution_query = Column(String(500), nullable=True)
    department_query = Column(String(500), nullable=True)
    result_name = Column(String(500), nullable=True)
    formatted_address = Column(Text, nullable=True)
    country = Column(String(100), nullable=True)
    province = Column(String(100), nullable=True)
    city = Column(String(100), nullable=True)
    district = Column(String(100), nullable=True)
    lat = Column(Float, nullable=True)
    lng = Column(Float, nullable=True)
    coordinate_system = Column(String(20), nullable=True)
    precision = Column(String(30), nullable=True)
    provider_confidence = Column(Float, nullable=True)
    raw_response = Column(JSON_TYPE, nullable=True)
    audit_status = Column(String(30), nullable=False, default="candidate", index=True)
    conflict_reason = Column(Text, nullable=True)
    checked_at = Column(DateTime(timezone=True), default=lambda: datetime.datetime.now(datetime.UTC))


class ArticleDraft(DraftBase):
    """推文/文章草稿表"""
    __tablename__ = "article_drafts"
    __table_args__ = (Index("ix_article_drafts_status", "status"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    draft_type = Column(String(50), nullable=False)  # single_daily, review, collection, manual_upload
    title = Column(String(500), nullable=False)
    title_zh = Column(String(500), nullable=True)
    content = Column(Text, nullable=False)  # 完整 HTML/Markdown 内容
    status = Column(String(50), default="draft", nullable=False)  # draft -> reviewed -> approved -> published
    reviewer = Column(String(100), nullable=True)
    reviewed_at = Column(DateTime(timezone=True), nullable=True)
    review_notes = Column(Text, nullable=True)  # 审核备注
    published_at = Column(DateTime(timezone=True), nullable=True)
    meta = Column(JSON_TYPE, nullable=True)  # 生成参数、模型版本等
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.datetime.now(datetime.UTC))
    updated_at = Column(DateTime(timezone=True), default=lambda: datetime.datetime.now(datetime.UTC), onupdate=lambda: datetime.datetime.now(datetime.UTC))


class ChannelAccount(AccountBase):
    """渠道多账号（一渠道可绑多个账号）"""
    __tablename__ = "channel_accounts"

    id              = Column(Integer, primary_key=True, autoincrement=True)
    channel         = Column(String(50), nullable=False, index=True)   # csdn/zhihu/baijiahao/xiaohongshu/sohu/bilibili
    account_name    = Column(String(100), nullable=False)              # 显示名，如"科研机构官方账号"
    credential_type = Column(String(20), default="cookie")            # cookie / oauth
    credential      = Column(Text, nullable=True)                      # 凭据；启用密钥时以 fernet: 前缀加密存储
    expires_at          = Column(DateTime(timezone=True), nullable=True)
    is_active           = Column(Boolean, default=True)
    bound_at            = Column(DateTime(timezone=True), default=lambda: datetime.datetime.now(datetime.UTC))
    note                = Column(Text, nullable=True)
    created_at          = Column(DateTime(timezone=True), default=lambda: datetime.datetime.now(datetime.UTC))
    updated_at          = Column(DateTime(timezone=True), default=lambda: datetime.datetime.now(datetime.UTC),
                                 onupdate=lambda: datetime.datetime.now(datetime.UTC))
    # 实际验证状态（headless 浏览器真实检测）
    last_validated_at   = Column(DateTime(timezone=True), nullable=True)
    validation_status   = Column(String(20), default="unknown")  # valid / invalid / unknown / error

class PublishRecord(DraftBase):
    """每次发布一条记录"""
    __tablename__ = "publish_records"
    __table_args__ = (
        Index("ix_publish_records_status", "status"),
        Index("ix_publish_records_created_at", "created_at"),
        UniqueConstraint(
            "draft_id", "account_id", "idempotency_key",
            name="uq_publish_record_draft_account_idempotency_key",
        ),
    )

    id           = Column(Integer, primary_key=True, autoincrement=True)
    draft_id     = Column(Integer, ForeignKey("article_drafts.id"), nullable=False, index=True)
    account_id   = Column(Integer, ForeignKey("channel_accounts.id", ondelete="SET NULL"), nullable=True, index=True)
    channel      = Column(String(50))            # 冗余字段方便查询
    account_name = Column(String(100))
    status       = Column(String(20), default="pending")   # pending/publishing/success/failed/scheduled/cancelled
    url          = Column(Text, nullable=True)
    error        = Column(Text, nullable=True)
    scheduled_at = Column(DateTime(timezone=True), nullable=True)         # 定时发布时间
    published_at = Column(DateTime(timezone=True), nullable=True)
    created_at   = Column(DateTime(timezone=True), default=lambda: datetime.datetime.now(datetime.UTC))
    hidden       = Column(Boolean, default=False)          # 是否在发件箱中隐藏
    idempotency_key = Column(String(255), nullable=True)
    updated_at   = Column(DateTime(timezone=True), default=lambda: datetime.datetime.now(datetime.UTC), onupdate=lambda: datetime.datetime.now(datetime.UTC), nullable=True)

    # 关联
    draft = relationship("ArticleDraft", backref="publish_records")
    # Account is optional; deleting one leaves the publishing record intact.


# ══════════════════════════════════════════════════════════════════════
# Video publishing models
# ══════════════════════════════════════════════════════════════════════

class ChannelVideo(Base):
    """视频资源表"""
    __tablename__ = "channel_videos"

    id = Column(Integer, primary_key=True, autoincrement=True)
    title = Column(String(500), nullable=False)
    description = Column(Text, nullable=True)
    tags = Column(String(500), nullable=True)            # comma-separated
    file_path = Column(String(1000), nullable=True)      # local path (null for AI-generated videos until downloaded)
    file_size = Column(Integer, nullable=True)           # bytes
    duration = Column(Float, nullable=True)              # seconds
    cover_path = Column(String(1000), nullable=True)     # custom cover image
    width = Column(Integer, nullable=True)
    height = Column(Integer, nullable=True)
    status = Column(String(50), default="draft")         # draft, approved, published, error
    source_paper_id = Column(Integer, nullable=True)     # optional link to paper
    script = Column(Text, nullable=True)                 # LLM-generated video script
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.datetime.now(datetime.UTC))
    updated_at = Column(DateTime(timezone=True), default=lambda: datetime.datetime.now(datetime.UTC),
                        onupdate=lambda: datetime.datetime.now(datetime.UTC))


class VideoPublishRecord(Base):
    """视频发布记录（一次发布到一个平台）"""
    __tablename__ = "video_publish_records"

    id = Column(Integer, primary_key=True, autoincrement=True)
    video_id = Column(Integer, ForeignKey("channel_videos.id"), nullable=False, index=True)
    account_id = Column(Integer, nullable=True, index=True)  # channel_accounts.id
    channel = Column(String(50), nullable=False)              # platform: douyin/bilibili/kuaishou/...
    account_name = Column(String(100), nullable=True)
    status = Column(String(20), default="pending")            # pending/uploading/success/failed
    publish_url = Column(Text, nullable=True)                 # URL after publishing
    error = Column(Text, nullable=True)
    scheduled_at = Column(DateTime(timezone=True), nullable=True)
    published_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.datetime.now(datetime.UTC))

    video = relationship("ChannelVideo", backref="publish_records")


class Job(Base):
    """Durable work queue shared by API, worker, and scheduler processes."""

    __tablename__ = "jobs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('queued', 'running', 'succeeded', 'failed', 'cancelled')",
            name="ck_jobs_status",
        ),
        Index("ix_jobs_claim", "status", "priority", "run_after", "created_at"),
        Index("ix_jobs_kind_status", "kind", "status"),
        UniqueConstraint("dedupe_key", name="uq_jobs_dedupe_key"),
    )

    id = Column(String(64), primary_key=True)
    kind = Column(String(80), nullable=False, index=True)
    payload = Column(JSON_TYPE, nullable=False)
    status = Column(String(20), nullable=False, default="queued", index=True)
    priority = Column(Integer, nullable=False, default=0)
    run_after = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.datetime.now(datetime.UTC))
    attempts = Column(Integer, nullable=False, default=0)
    max_attempts = Column(Integer, nullable=False, default=3)
    locked_by = Column(String(180), nullable=True)
    locked_at = Column(DateTime(timezone=True), nullable=True)
    heartbeat_at = Column(DateTime(timezone=True), nullable=True)
    result = Column(JSON_TYPE, nullable=True)
    error = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.datetime.now(datetime.UTC))
    updated_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.datetime.now(datetime.UTC), onupdate=lambda: datetime.datetime.now(datetime.UTC))
    dedupe_key = Column(String(255), nullable=True)


class SchedulerLease(Base):
    """Lease row used to elect one scheduler across SQLite and PostgreSQL."""

    __tablename__ = "scheduler_leases"

    name = Column(String(80), primary_key=True)
    locked_by = Column(String(180), nullable=False)
    locked_until = Column(DateTime(timezone=True), nullable=False)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.datetime.now(datetime.UTC))


# ══════════════════════════════════════════════════════════════════════
# DingTalk SSO user mapping
# ══════════════════════════════════════════════════════════════════════

class DingTalkUser(AccountBase):
    """钉钉用户映射表 - 将钉钉用户 ID 映射到内部用户名和角色"""
    __tablename__ = "dingtalk_users"

    id = Column(Integer, primary_key=True, autoincrement=True)
    dingtalk_userid = Column(String(100), unique=True, nullable=False, index=True)
    internal_username = Column(String(50), nullable=False, index=True)
    role = Column(String(20), nullable=False)
    display_name = Column(String(100), nullable=True)  # 钉钉用户姓名
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.datetime.now(datetime.UTC))
    updated_at = Column(DateTime(timezone=True), default=lambda: datetime.datetime.now(datetime.UTC), onupdate=lambda: datetime.datetime.now(datetime.UTC))
