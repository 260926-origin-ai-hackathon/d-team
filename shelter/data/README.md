# shelter/data — 避難所AIに同梱する公開データ

取得日 2026-09-25。取得は `python scripts/fetch_public_data.py`（PDF/PNG/JPG/XLSX は git に入れない。`.txt` と `.md` だけコミットする）。
PDF のテキスト抽出は `pypdf`（`<<page N>>` 区切りで同名 `.txt`）。

## manual/ — 運営マニュアル（RAG の根拠）
| ファイル | 内容 | 発行 | ページ / 抽出文字数 | 利用条件 |
|---|---|---|---|---|
| `2412hinanjo_guideline.pdf` | 内閣府「避難所運営等避難生活支援のためのガイドライン（チェックリスト）」 | 平成28年4月（令和6年12月改定） | 84p / 57,538字 | 政府標準利用規約（出典明記で利用可） |
| `2412kankyokakuho.pdf` | 内閣府「避難所における良好な生活環境の確保に向けた取組指針」 | 平成25年8月（令和6年12月改定） | 32p / 31,783字 | 同上 |
| `1604hinanjo_toilet_guideline.pdf` | 内閣府「避難所におけるトイレの確保・管理ガイドライン」 | 平成28年4月 | 30p / 24,795字 | 同上 |
| `osaka_hinanjo_guideline_honpen_R7.pdf` | 大阪市「避難所開設・運営ガイドライン」本編 | 平成29年5月（令和7年3月改訂） | 62p / 40,447字 | CC BY 4.0（大阪市） |
| `osaka_hinanjo_guideline_shiryo_R7.pdf` | 同 資料編（呼びかけ文例・参考資料） | 同上 | 14p / 9,646字 | CC BY 4.0 |
| `osaka_hinanjo_guideline_youshiki_R7.xlsx` | 同 様式1〜25（名簿・報告様式など） | 同上 | — | CC BY 4.0 |
| `osaka_pet_guide.pdf` | 大阪市「災害時のペット対策 ペット同行避難ガイドライン」 | 令和7年3月 | 21p / 12,301字 | CC BY 4.0 |
| `osaka_pet_manual.pdf` | 大阪市「ペットの一時飼育場所 開設運営マニュアル（ひな型）」 | — | 12p / 6,331字 | CC BY 4.0 |
| `saitama_seibu_kyumei3.pdf` | 埼玉西部消防局「普通救命講習テキストⅢ 〜救急車がくるまでに〜」（心肺蘇生・AED・止血・応急手当） | — | 12p / 9,601字 | 消防局公開資料（出典明記） |
| `mhlw_shougaiji_hairyo_R1.pdf` | 厚生労働省 事務連絡「避難所等で生活する障害児者への配慮事項等について」 | 令和元年10月 | 4p / 2,607字 | 政府標準利用規約 |
| `first_aid_essentials.ja.md` | 避難所での応急手当の要点（心肺蘇生・止血・骨折・やけど・窒息・熱中症・低体温・エコノミークラス症候群・衛生・持病・こころ）。Claude が公的資料から要点化 | 2026-09-25 | — | 出典は文書内 |

取得を試みて使えなかったもの: 京都市消防局 救命講習テキスト（フォント埋め込みでテキスト抽出が文字化け）、内閣府「熱中症を予防しよう」（AES 暗号化 PDF・抽出が文字化け）、消防庁 WEB 講習の案内 PDF（講習サイトの説明のみ）。いずれも要点は `first_aid_essentials.ja.md` に反映。

出典 URL: 内閣府 https://www.bousai.go.jp/taisaku/hinanjo/index.html ／ 大阪市 https://www.city.osaka.lg.jp/kikikanrishitsu/page/0000474277.html
RAG の優先順（同点なら）: 大阪市本編 → 内閣府ガイドライン → 環境確保指針 → トイレ → ペット。

## hazard/ — 都島区 水害ハザードマップ
`miyakojima_hazard_summary.md` に東野田町（QUINTBRIDGE）周辺の要約。原本は啓発面・地図面・追加分（日英）と災害種別 PNG。地図面は画像のみ。日本語啓発面はテキスト抽出が文字化けするため英語版の `.txt` を使う。
出典: https://www.city.osaka.lg.jp/kikikanrishitsu/page/0000300781.html ／ 多言語 https://www.city.osaka.lg.jp/kikikanrishitsu/page/0000522880.html

## local/ — 都島区の避難所・防災マップ
`miyakojima_shelters.md` に区の指定避難所・一時避難場所・津波避難ビル・広域避難場所・連絡先を転記（原本 `miyakojima_shelter_list_2026.jpg/.pdf`、都島区防災マップ 2026 日英 PDF）。
出典: https://www.city.osaka.lg.jp/miyakojima/page/0000002475.html

## まだ無いもの
- `shelter_info.ja.md` / `.en.md`（避難所のルール・設備。避難所名は**東高等学校 避難所**（東野田町4-15-14）に決定 2026-09-25）
- `faq.ja.md` / `.en.md`（`docs/research/03-information.md` の33件の質問例から作る）
