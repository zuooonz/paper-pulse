# 期刊参考数据说明

本目录与仓库代码采用不同许可：仓库代码采用 MIT 许可（见根目录 `/LICENSE`），本目录下来自 ShowJCR 项目的数据文件按 **GPL-3.0** 再分发（见本目录的 `LICENSE.GPL-3.0`）。

## 来源

| 项目 | 内容 |
|---|---|
| 上游项目 | ShowJCR — https://github.com/hitfyd/ShowJCR |
| 上游许可 | GPL-3.0 |
| 文件 | `jcr.db` (SQLite) |
| 上游路径 | `中科院分区表及JCR原始数据文件/jcr.db` |
| 上游提交 | `4687a8f608f20aaf004299992f5d347dba009361` (2026-09-23) |
| SHA-256 | `1cedb15c7a18107d7cb53ee99efa8fd4699fcbb15b046f03fdac764c8854c607` |
| 获取日期 | 2026-10-21 |

上游项目说明的内容包括：2025、2024 年 JCR 影响因子和 JCR 分区；中科院期刊分区表（中科院分区表升级版，2025 年及更早版本）；新锐期刊分区表（2026 年）；国际期刊预警名单；以及 CCF 推荐目录。上游项目注明，这些目录的原始发布方包括 fenqubiao.com、xr-scholar.com、ccf.org.cn 等；详情请见上游 README。使用者有责任遵守原始数据提供方的条款。

感谢 ShowJCR 项目维护者整理并公开这些数据。

## 更新

运行 `python src/tools/update_jcr_db.py --force` 获取最新上游版本，然后更新上方表格中的提交编号和哈希值。应用使用的表名和列名在 `config/reference.yaml` 中配置。
