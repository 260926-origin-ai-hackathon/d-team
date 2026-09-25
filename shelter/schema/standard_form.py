"""避難者カード標準様式（案）の項目定義。CSV 出力・受付フォーム・集計が参照する唯一の場所。

※「内閣府の検討会で示された標準化案（資料3-1）」に沿う。正式な全国標準かは未確認。
"""
from __future__ import annotations

SEX = {"M": "男", "F": "女", "X": "回答しない・その他"}
SEX_EN = {"M": "Male", "F": "Female", "X": "Prefer not to say / Other"}

# 要配慮の区分コード（QR ペイロード care[]）
CARE = {
    "ILL": "病気・けが",
    "PRG": "妊産婦",
    "INF": "乳幼児同伴",
    "DIS": "障がい",
    "NUR": "要介護",
    "MED": "医療機器（透析・酸素等）",
    "ALG": "アレルギー",
}
CARE_EN = {
    "ILL": "Illness / injury",
    "PRG": "Pregnant / postpartum",
    "INF": "With infant",
    "DIS": "Disability",
    "NUR": "Needs nursing care",
    "MED": "Medical device (dialysis, oxygen)",
    "ALG": "Allergy",
}

# アレルギーの内訳（alg[]）
ALLERGY = {
    "egg": "卵",
    "milk": "乳",
    "wheat": "小麦",
    "peanut": "落花生",
    "soba": "そば",
    "shrimp": "えび",
    "crab": "かに",
    "other": "その他",
}

LANGS = {"ja": "日本語", "en": "English"}

SOURCE = {"qr": "QR", "manual": "手入力", "app": "スマホ"}

# 「避難所の運営に伝えたいこと」（拡張・自己登録の選択肢。needs[]）。
# 標準様式の要配慮区分（CARE）が「属性」なのに対し、こちらは「いま必要な支援」。上ほど急ぐ
NEEDS = {
    "dialysis": "透析に通う必要がある",
    "oxygen": "酸素・医療機器の電源が必要",
    "medicine": "毎日の薬が切れそう・無い",
    "doctor": "診察・けがの手当てが必要",
    "mobility": "歩く・移動に手助けが要る",
    "baby": "粉ミルク・おむつが必要",
    "diet": "食事の制限がある（アレルギー・宗教）",
    "family": "家族と連絡が取れない",
    "sleep": "不安が強い・眠れない",
    "language": "日本語がわからない",
}
NEEDS_EN = {
    "dialysis": "I need dialysis",
    "oxygen": "I need power for oxygen / medical device",
    "medicine": "I am out of (or running out of) daily medicine",
    "doctor": "I need to see a doctor / wound care",
    "mobility": "I need help walking / moving",
    "baby": "I need baby formula / diapers",
    "diet": "I have dietary restrictions (allergy / religion)",
    "family": "I cannot reach my family",
    "sleep": "I feel very anxious / cannot sleep",
    "language": "I do not understand Japanese",
}
# 急ぎの度合い（運営画面の並び順に使う。大きいほど上）
NEEDS_URGENCY = {"dialysis": 5, "oxygen": 5, "medicine": 4, "doctor": 4, "mobility": 2,
                 "baby": 3, "diet": 2, "family": 2, "sleep": 1, "language": 1}

NOTE_MAX = 200

# 同行者（家族）の続柄（members[].rel）。受付1件＝代表者＋同行者。同行者は受付番号を分けない
REL = {"spouse": "配偶者", "child": "子", "parent": "親", "grandparent": "祖父母", "grandchild": "孫",
       "sibling": "きょうだい", "relative": "親せき", "other": "その他"}
REL_EN = {"spouse": "Spouse", "child": "Child", "parent": "Parent", "grandparent": "Grandparent",
          "grandchild": "Grandchild", "sibling": "Sibling", "relative": "Relative", "other": "Other"}
MEMBERS_MAX = 29  # 同行者の上限（本人を含めて 30 人）

# 名簿 CSV の列（標準様式順）。(見出し, 行 dict → 値 の関数名) は export 側で解決
ROSTER_COLUMNS = [
    "受付番号", "入所日時", "退所日時", "氏名", "ふりがな", "性別", "生年月日", "年齢",
    "住所", "世帯人数",
    "病気けが", "妊産婦", "乳幼児", "障がい", "要介護", "医療機器", "アレルギー",
    "アレルギー内訳", "備考", "運営に伝えたいこと(拡張)", "使用言語(拡張)", "ペット(拡張)", "受付方法", "端末ID",
    "続柄(拡張)",
]
# 名簿 CSV は1人1行。同行者は代表者と同じ受付番号で続け、「続柄(拡張)」に続柄を入れる（代表者は「本人」）

# CSV の要配慮列 → 区分コード
ROSTER_CARE_COLUMNS = {
    "病気けが": "ILL", "妊産婦": "PRG", "乳幼児": "INF", "障がい": "DIS",
    "要介護": "NUR", "医療機器": "MED", "アレルギー": "ALG",
}
