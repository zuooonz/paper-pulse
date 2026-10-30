# 脚本

本目录包含数据导入、补全、维护和本地服务管理命令。以下脚本从仓库根目录运行；先使用 `--help` 查看参数。涉及写库或文件清理的操作，请先阅读帮助并在支持时使用预览模式。

数据库脚本通常会先将数据库迁移到最新版本；需要跳过时使用 `--no-migrate` 或设置 `PAPER_PULSE_NO_MIGRATE=1`。

论文导入后，处理进度与审核结论独立保存：普通文献导入通常为 `done + unreviewed`，待补全记录可保持 `pending`；审核由管理员或审核员完成。PDF 可用性依据存储文件实际存在情况刷新，PDF 下载失败只记录到 `processing_error`，不会改变审核结论。

## 脚本索引

- `audit_author_geography.py`：核查作者单位地理信息并记录候选证据。
- `audit_database_integrity.py`：检查论文记录、字段和关系完整性。
- `auto_ingest.py`：处理 `pending` 论文的元数据、作者、摘要和 PDF 文本补全流程。
- `clean_legacy_taxonomy.py`：检查或清理不符合当前分类词表的旧分类值。
- `cleanup_orphan_pdfs.py`：预览或清理 PDF 目录中没有数据库记录的文件。
- `download_pdfs.py`：按论文记录批量下载 PDF。
- `download_plos.py`：为缺少全文的记录查找开放获取 PDF。
- `enhance_corresponding_affiliations.py`：补全通讯作者单位的地理和机构信息。
- `enhance_paper_tags.py`：依据论文证据补全结构化分类。
- `enrich_abstracts.py`：从元数据、索引服务和全文补全摘要，并可生成中文摘要。
- `enrich_brain_regions.py`：从摘要或全文提取配置化的结构化字段。
- `enrich_entities.py`：从论文全文识别配置的实体词条。
- `enrich_conclusions_highlights.py`：补全中文结论和研究要点。
- `enrich_countries.py`：补全通讯作者国家或地区。
- `enrich_crossref.py`：通过 Crossref 补充论文元数据。
- `enrich_dates.py`：提取接收日期和发表日期。
- `enrich_journal.py`：关联期刊并补充影响因子和分区信息。
- `enrich_missing_fields.py`：从已有 Crossref 元数据补全缺失字段。
- `enrich_months.py`：提取发表月份。
- `enrich_paper_metadata.py`：批量关联期刊并补充通用元数据。
- `enrich_received_date.py`：提取论文接收日期。
- `export_markdown.py`：将论文库导出为 Markdown 文件。
- `fill_missing_authors.py`：通过 Crossref 补充缺失作者信息。
- `fix_na_journals.py`：修复无效 ISSN 导致的期刊重复记录。
- `generate_from_db.py`：从数据库生成内容草稿。
- `generate_summaries.py`：为论文生成摘要草稿。
- `geocode_affiliations.py`：根据单位名称和城市信息补全地理坐标。
- `governance_maintenance.py`：备份 SQLite 应用数据库并归档旧审计事件；PostgreSQL 备份请使用相应数据库工具。
- `import_excel_papers.py`：从 Excel 文件导入论文记录。
- `import_pdf_batch.py`：批量导入本地 PDF 文件。
- `ingest_downloaded_pdfs.py`：扫描并关联已下载的 PDF 文件。
- `llm_enrich_paper.py`：使用模型补全单篇论文的结构化字段。
- `llm_fill_missing_fields.py`：使用模型补全缺失的摘要、结论和要点。
- `literature_search.py`：按指定时间范围检索实体相关论文并入库。
- `migrate_sqlite_to_single_db.py`：一次性将旧版论文、账号、草稿和治理 SQLite 文件导入统一数据库。
- `playwright_login_helper.py`：通过浏览器辅助完成发布渠道登录。
- `preview_word_style.py`：预览 Word 文档样式。
- `rebuild_apa_all.py`：重建论文引用并重新导出。
- `reconcile_pdf_flags.py`：分批检查 PDF 文件并修正论文的 `has_pdf` 标志。
- `run_pdf_pipeline.py`：运行 PDF 关联和文本提取流程。
- `score_drafts.py`：计算并保存草稿质量分数。
- `search_domain_papers.py`：按领域包配置搜索并导入论文。
- `seed_demo_data.py`：写入供新用户体验和界面截图使用的虚构演示数据；重复运行不会重复插入，可使用 `--reset` 重建。
- `server.py`：启动、停止或查看本地服务状态。
- `update_all_impact_factors.py`：按期刊参考数据更新论文的期刊指标和分区信息。
- `verify_channel_cookies.py`：检查发布渠道登录状态和 Cookie 是否有效。

## 用法示例

```bash
python scripts/run_pdf_pipeline.py --help
python scripts/enrich_abstracts.py --limit 20 --dry-run
python scripts/literature_search.py --help
python scripts/cleanup_orphan_pdfs.py --help
python scripts/reconcile_pdf_flags.py
python scripts/seed_demo_data.py
```

数据库脚本通过统一的 `DATABASE_URL` 读取配置。已有旧版多文件 SQLite 数据的用户应先备份，再运行 `PYTHONPATH=src python scripts/migrate_sqlite_to_single_db.py` 一次性导入；脚本会报告源表与目标表行数。
