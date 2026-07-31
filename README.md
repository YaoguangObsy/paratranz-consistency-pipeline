一个基于术语表的翻译一致性校对工作台。

_仍在调试中的个人工具 Built with Claude_

### 工作流：

```
0. 配置token和项目id
1. 从paratranz导出术语表、文本包
2. 基于特征统计的一致性比对；未经优化
3. 生成决策表，继承旧决策
4. ai辅助决策；预留接口，未启用
5. 将修改推送至远程项目
```

### 文件结构

```
paratranz-consistency-pipeline/
  config.py
  push_receipt.csv      <- 推送日志
  review_decisions.csv  <- 决策表
  data/
    excluded_keys.txt       <- 比对前过滤的已隐藏词条key
    Merged.csv              <- 合并自文本包的本地化文本
    Original_En.csv         <- 原语言文本
    paratranz_terms.csv     <- 术语表
  reports/
    inconsistency_duplicate_source.csv
    inconsistency_glossary.csv      <- 不一致报告
  scripts/
    1_fetch_paratranz_artifacts.py
    1_fetch_paratranz_hidden_keys.py
    1_fetch_paratranz_terms.py
    2_check_consistency.py
    3_build_review_sheet.py
    4_ai_assist_fill.py
    5_push_to_paratranz.py
  webapp/
    app.py
    templates/
        index.html
    static/
        app.js
        style.css
```

## 安装 & 运行

```bash
pip install -r requirements.txt # 包含ai库 - anthropic
cd webapp
python app.py
```

浏览器打开 http://127.0.0.1:5001

