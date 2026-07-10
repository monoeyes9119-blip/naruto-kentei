#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
NARUTO検定データ統合スクリプト。
6つのraw_*.json + generated_extra.json を読み込み、
- 信頼できないデータを needs_review.json に隔離
- ①②③④選択肢を choices 配列に分離
- カテゴリ付与
- 重複排除
を行い questions_master.json と app/questions.js を出力する。
"""
import json
import re
import os

BASE = os.path.dirname(os.path.abspath(__file__))
RAW_FILES = [
    "raw_p02-15.json",
    "raw_p16-29.json",
    "raw_p30-43.json",
    "raw_p44-57.json",
    "raw_p58-71.json",
    "raw_p72-85.json",
]
GENERATED_FILES = ["generated_extra.json", "generated_part1.json", "generated_deep.json", "generated_batch4.json", "generated_batch5.json", "generated_batch6.json", "generated_batch7.json"]
REVIVED_FILE = "revived.json"

CIRCLE_MARKS = ["①", "②", "③", "④"]

# 元々4択だった選択肢が1つの答えに潰れており、正解が不確かなため
# needs_review行きにする特定の問題（question文字列の前方一致で判定）
UNCERTAIN_QUESTION_PREFIXES = [
    "ナルトがイタチに幻術をかけられたときに起こったことは",
]

UNRELIABLE_NOTE_PHRASES = [
    "判読不明瞭",
    "丸印なし",
    "丸印が写真から判読不明瞭",
    "正解不明",
]


def load(fname):
    with open(os.path.join(BASE, fname), encoding="utf-8") as f:
        return json.load(f)


def normalize(s):
    """空白除去した正規化文字列（重複判定用）"""
    if s is None:
        return ""
    return re.sub(r"\s+", "", s)


def split_choices_from_text(text):
    """「①…②…③…④…」形式のテキストから前置き文と4選択肢を分離する。
    戻り値: (prefix, [choice1..4]) または (text, None) if not found
    """
    if not text or "①" not in text:
        return text, None
    # ①の開始位置を境に前置きと選択肢部分を分ける
    idx1 = text.find("①")
    prefix = text[:idx1].strip()
    choice_part = text[idx1:]

    # ①②③④ の位置で分割
    positions = []
    for mark in CIRCLE_MARKS:
        pos = choice_part.find(mark)
        if pos == -1:
            return text, None
        positions.append(pos)
    if positions != sorted(positions):
        return text, None

    choices = []
    for i, mark in enumerate(CIRCLE_MARKS):
        start = positions[i] + len(mark)
        end = positions[i + 1] if i + 1 < len(CIRCLE_MARKS) else len(choice_part)
        raw_choice = choice_part[start:end]
        # 末尾の「（丸印は...）」等の注記や句読点を除去
        raw_choice = re.sub(r"[。\.\s]*$", "", raw_choice).strip()
        raw_choice = re.sub(r"\s+", " ", raw_choice)
        choices.append(raw_choice)

    if len(choices) != 4 or any(not c for c in choices):
        return text, None
    return prefix, choices


def strip_marker(answer):
    """answerの先頭に①②③④が付いていれば除去してテキスト部分を返す"""
    if not answer:
        return answer
    a = answer.strip()
    if a and a[0] in CIRCLE_MARKS:
        return a[1:].strip()
    return a


def has_unreliable_note(note):
    if not note:
        return False
    return any(p in note for p in UNRELIABLE_NOTE_PHRASES)


# 選択肢の存在を前提にした問題文パターン（choicesが無いと成立しない）
CHOICE_DEPENDENT_RE = re.compile(
    r"含まれない|当てはまらない|正しいもの|間違って|次のうち|どれか"
)

# ---------------------------------------------------------------
# 難易度付与
# ---------------------------------------------------------------
# 検定過去問: 出典試験 → 難易度（下忍=易 / 中忍=普 / 上忍=難）
# ファイル名とページ番号の対応（ページ全体を一つの試験とみなす粗い判定でよい）:
#   raw_p30-43.json          = 上忍試験第1回                    → 難
#   raw_p44-57.json          = 上忍試験/中忍試験混在
#                               (p44-47=上忍寄り, p48-57=中忍寄り) → 難/普
#   raw_p58-71.json          = 中忍試験(問055-097)+下忍試験(問001-030)
#                               (p58-65=中忍, p66-71=下忍)        → 普/易
#   raw_p72-85.json          = 下忍試験(問031-097)               → 易
#   raw_p02-15.json/p16-29.json = 原作一問一答（試験ではない）
EXAM_NAME_TO_DIFFICULTY = {
    "上忍試験": "難",
    "中忍試験": "普",
    "下忍試験": "易",
}

# ファイル名 → 試験名判定関数群（noteに明記が無い場合のフォールバック）
#
# PDF再読解で確認した実際の試験境界（2026-07-08）:
#   PDF page28〜46 = 第2回NARUTO検定「上忍試験」(問001〜094が該当、raw_p16-29.jsonの
#                     page28-29分とraw_p30-43.json全体、raw_p44-57.jsonのpage44-46分)
#   PDF page47      = 上忍試験の末尾(問095-097)と中忍試験の冒頭(問001-004)が同一写真
#                      (見開き2ページ)に混在する特殊ページ。question文字列で判別する。
#   PDF page47(中忍分)〜65 = 中忍試験
#   PDF page66〜85  = 下忍試験
#
# 見開き混在ページ(page47)で中忍試験側に属すると判定するための固有キーワード
PAGE47_CHUNIN_MARKERS = ["音忍三人組", "油女一族", "点穴", "予選の第四回戦"]


def exam_name_from_file_and_page(source_file, page):
    """_source_fileとpage番号から試験名を推定する。該当なければNoneを返す。"""
    if source_file == "raw_p16-29.json":
        # page28以降は「第2回NARUTO検定 上忍試験」の問001〜が始まっている
        if page is not None and page >= 28:
            return "上忍試験"
        return None
    if source_file == "raw_p30-43.json":
        return "上忍試験"
    if source_file == "raw_p44-57.json":
        if page is not None and page <= 46:
            return "上忍試験"
        return "中忍試験"
    if source_file == "raw_p58-71.json":
        if page is not None and page >= 66:
            return "下忍試験"
        return "中忍試験"
    if source_file == "raw_p72-85.json":
        return "下忍試験"
    return None


def assign_exam(m):
    """検定過去問カテゴリの問題に試験名(下忍試験/中忍試験/上忍試験)を付与する。
    原作一問一答・オリジナル新作はNone。
    """
    if m.get("_category") != "検定過去問":
        return None
    # revived.json由来はexamが確定済み
    if m.get("_exam"):
        return m["_exam"]
    note = m.get("_note") or ""
    question = m.get("question") or ""
    for exam in ("上忍試験", "中忍試験", "下忍試験"):
        if exam in note or exam in question:
            return exam
    source_file = m.get("_source_file")
    page = m.get("page")
    # page47は上忍試験の末尾と中忍試験の冒頭が同一写真に混在する特殊ページ
    if source_file == "raw_p44-57.json" and page == 47:
        if any(kw in question for kw in PAGE47_CHUNIN_MARKERS):
            return "中忍試験"
        return "上忍試験"
    return exam_name_from_file_and_page(source_file, page)

# 主要キャラ・有名な術（答えがこれらを含む一問一答は「易」）
FAMOUS_TERMS = [
    "ナルト", "サスケ", "サクラ", "カカシ", "イタチ", "我愛羅", "綱手",
    "自来也", "大蛇丸", "シカマル", "ヒナタ", "九尾", "ペイン", "マダラ",
    "螺旋丸", "写輪眼", "白眼", "千鳥", "影分身", "木ノ葉隠れ",
]


def heuristic_difficulty(answer):
    """原作一問一答のヒューリスティック難易度判定。"""
    a = (answer or "").strip()
    # 難: 数値系、台詞の完全再現（「」入り長文）、3行以上
    if a.count("\n") >= 2:
        return "難"
    if re.search(r"\d", a):
        return "難"
    if len(a) >= 20:
        return "難"
    if ("「" in a or "」" in a) and len(a) >= 10:
        return "難"
    # 易: 主要キャラ名や有名な術を含む
    if any(t in a for t in FAMOUS_TERMS):
        return "易"
    return "普"


def assign_difficulty(m):
    """統合済みアイテムに難易度を付与する。優先順位:
    1. 質問文に「三禁」を含む → 普(category=原作一問一答に強制)
    2. generated_extra.json由来 / revived.json由来 → 既存のdifficultyをそのまま使用
    3. 検定過去問（choicesあり、またはraw由来でpage30〜85） → 試験名から判定
    4. 原作一問一答（それ以外） → ヒューリスティック判定
    5. フォールバック → 普
    """
    # 1. 三禁問題は特別扱い（category_hintも原作一問一答に強制）
    if "三禁" in (m.get("question") or ""):
        return "普"

    # 2. オリジナル新作（generated_*）・PDF再読解で復活済み（revived.json）はそのまま使用
    if m.get("_source_file", "").startswith("generated") or m.get("_source_file") == REVIVED_FILE:
        return m.get("difficulty") or "普"

    # 3. 検定過去問判定: choicesがある、またはpageが30〜85の範囲
    page = m.get("page")
    has_choices = bool(m.get("choices"))
    in_exam_page_range = page is not None and 30 <= page <= 85
    if has_choices or in_exam_page_range:
        exam = m.get("exam") or assign_exam(m)
        if exam:
            return EXAM_NAME_TO_DIFFICULTY[exam]
        return "普"

    # 4. 原作一問一答はヒューリスティック
    return heuristic_difficulty(m.get("answer"))


def question_match_key(question):
    """choicesが埋め込まれた質問文から設問部分だけを取り出して正規化する。
    revived.json内の（choices分離済みの）質問文との照合キーとして使う。"""
    q = question or ""
    if "①" in q:
        q = q.split("①")[0]
    return normalize(q)


def process_raw_item(item, review_list, revived_keys=frozenset()):
    """1件のrawアイテムを処理し、正常なら統合用dictを返す。除外ならNoneを返しreview_listに追記する。
    revived_keysに含まれる質問（=PDF再読解でrevived.jsonへ復活済み）は、review_listへの
    二重登録を避けるためneeds_reviewに積まず静かにNoneを返す。
    """
    question = (item.get("question") or "").strip()
    answer_raw = (item.get("answer") or "").strip()
    note = item.get("note") or ""
    volume = item.get("volume")
    page = item.get("page")

    if question_match_key(question) in revived_keys:
        return None

    # --- 1. 明らかに使えないものを弾く ---
    if not question or question in ("（見出しのみ、質問文不明）",) or "質問文不明" in question or "質問文が" in question:
        review_list.append({**item, "_reject_reason": "questionがプレースホルダ/質問文不明"})
        return None

    if any(question.startswith(p) for p in UNCERTAIN_QUESTION_PREFIXES):
        review_list.append({**item, "_reject_reason": "元4択が答えに潰れており正解が不確か・要ユーザー確認"})
        return None

    if not answer_raw:
        review_list.append({**item, "_reject_reason": "answerが空"})
        return None

    if answer_raw == "判読不明瞭":
        review_list.append({**item, "_reject_reason": "answerが判読不明瞭"})
        return None

    if answer_raw in ("?", "？"):
        review_list.append({**item, "_reject_reason": "answerが記号のみで信頼できない"})
        return None

    if has_unreliable_note(note):
        review_list.append({**item, "_reject_reason": f"noteに信頼性を損なう記載あり: {note}"})
        return None

    # --- 2. 選択肢抽出: question内 ---
    choices = None
    q_body = question
    if "①" in question:
        prefix, extracted = split_choices_from_text(question)
        if extracted:
            q_body = prefix
            choices = extracted
        else:
            review_list.append({**item, "_reject_reason": "question内の①②③④選択肢パース失敗"})
            return None

    # --- 3. 選択肢抽出: note内 ---
    if choices is None and "①" in note:
        # note全体から①開始位置以降を選択肢とみなす
        idx1 = note.find("①")
        choice_part = note[idx1:]
        _, extracted = split_choices_from_text(choice_part)
        if extracted:
            choices = extracted
        else:
            review_list.append({**item, "_reject_reason": "note内の①②③④選択肢パース失敗"})
            return None

    # --- 3.5 三禁問題の特別書き換え（選択肢欠落チェックより先に処理） ---
    if "三禁" in q_body and choices is None:
        return {
            "page": page,
            "volume": volume,
            "question": "自来也がナルトに教えた、忍が抱く『三禁』とは何か？",
            "answer": "酒・女・金（かね）",
            "choices": None,
            "difficulty": "普",
            "_force_category": "原作一問一答",
        }

    # --- 3.6 選択肢欠落チェック: choicesが無いのに選択肢前提の問題文 ---
    if choices is None and CHOICE_DEPENDENT_RE.search(q_body):
        review_list.append({**item, "_reject_reason": "選択肢欠落のため成立しない"})
        return None

    # --- 4. answerの整形＆choicesとの整合確認 ---
    answer_final = strip_marker(answer_raw)

    if choices:
        # answerがchoicesのどれかと一致するか確認（前後空白差異は許容）
        norm_choices = [normalize(c) for c in choices]
        norm_answer = normalize(answer_final)
        if norm_answer not in norm_choices:
            # 完全一致しない場合、部分一致（answerがchoiceの部分文字列 or 逆）を試す
            match_idx = None
            for i, nc in enumerate(norm_choices):
                if norm_answer and (norm_answer in nc or nc in norm_answer):
                    match_idx = i
                    break
            if match_idx is None:
                review_list.append({
                    **item,
                    "_reject_reason": f"answerがchoicesと不一致 answer={answer_final!r} choices={choices!r}",
                })
                return None
            else:
                answer_final = choices[match_idx]

    q_body = re.sub(r"^問\d+[：:\s　]*", "", q_body.strip())

    result = {
        "page": page,
        "volume": volume,
        "question": q_body.strip(),
        "answer": answer_final.strip(),
        "choices": choices,
        "_note": note,
    }
    return result


def process_generated_item(item, review_list):
    question = (item.get("question") or "").strip()
    answer = (item.get("answer") or "").strip()
    if not question or not answer:
        review_list.append({**item, "_reject_reason": "questionまたはanswerが空"})
        return None
    return {
        "page": None,
        "volume": item.get("volume"),
        "question": question,
        "answer": answer,
        "choices": None,
        "difficulty": item.get("difficulty"),
        "category_hint": item.get("category"),
    }


def process_revived_item(item):
    """PDF再読解で復活させたrevived.json内の1件を統合用dictに変換する。
    revived.jsonのcategory/difficulty/exam/choicesはPDF再読解時に人手で確定済みのため
    そのまま使用する（process_raw_itemのようなchoices抽出・整合チェックは不要）。
    """
    return {
        "page": item.get("page"),
        "volume": item.get("volume"),
        "question": (item.get("question") or "").strip(),
        "answer": item.get("answer"),
        "choices": item.get("choices"),
        "_note": None,
        "_category": item.get("category") or "原作一問一答",
        "difficulty": item.get("difficulty"),
        "_exam": item.get("exam"),
    }


def assign_category(result, source):
    if result.get("_force_category"):
        return result["_force_category"]
    if source == "generated":
        return "オリジナル新作"
    if result.get("choices"):
        return "検定過去問"
    if result.get("volume"):
        return "原作一問一答"
    return "原作一問一答"


def main():
    review_list = []
    merged = []

    # revived.json（PDF再読解でneeds_reviewから復活させた問題）を先に読み込み、
    # rawファイル再処理時にneeds_reviewへ二重登録しないためのキー集合を作る
    revived_items = load(REVIVED_FILE) if os.path.exists(os.path.join(BASE, REVIVED_FILE)) else []
    revived_keys = {question_match_key(ri.get("question")) for ri in revived_items}

    for fname in RAW_FILES:
        items = load(fname)
        for item in items:
            processed = process_raw_item(item, review_list, revived_keys)
            if processed:
                processed["_source_file"] = fname
                processed["_category"] = assign_category(processed, "raw")
                merged.append(processed)

    for gen_file in GENERATED_FILES:
        generated_items = load(gen_file)
        for item in generated_items:
            processed = process_generated_item(item, review_list)
            if processed:
                processed["_source_file"] = gen_file
                processed["_category"] = assign_category(processed, "generated")
                merged.append(processed)

    for item in revived_items:
        processed = process_revived_item(item)
        processed["_source_file"] = REVIVED_FILE
        merged.append(processed)

    # --- 重複排除（質問文正規化で同一のものは1つに） ---
    seen = {}
    deduped = []
    dup_count = 0
    for m in merged:
        key = normalize(m["question"])
        if key in seen:
            dup_count += 1
            # 既存よりchoicesありを優先して差し替え
            existing = seen[key]
            if not existing.get("choices") and m.get("choices"):
                idx = deduped.index(existing)
                deduped[idx] = m
                seen[key] = m
            continue
        seen[key] = m
        deduped.append(m)

    # --- 試験名付与（検定過去問のみ。難易度付与より先に行う） ---
    for m in deduped:
        m["exam"] = assign_exam(m)

    # --- 難易度付与（全問必須） ---
    for m in deduped:
        m["difficulty"] = assign_difficulty(m)

    # --- questions_master.json 出力 ---
    master_path = os.path.join(BASE, "questions_master.json")
    with open(master_path, "w", encoding="utf-8") as f:
        json.dump(deduped, f, ensure_ascii=False, indent=2)

    # --- needs_review.json 出力 ---
    review_path = os.path.join(BASE, "needs_review.json")
    with open(review_path, "w", encoding="utf-8") as f:
        json.dump(review_list, f, ensure_ascii=False, indent=2)

    # --- app/questions.js 出力 ---
    app_questions = []
    for i, m in enumerate(deduped, start=1):
        entry = {
            "id": i,
            "volume": m.get("volume"),
            "category": m["_category"],
            "difficulty": m.get("difficulty", "普"),
            "question": m["question"],
            "answer": m["answer"],
        }
        if m.get("choices"):
            entry["choices"] = m["choices"]
        if m.get("exam"):
            entry["exam"] = m["exam"]
        app_questions.append(entry)

    js_path = os.path.join(BASE, "..", "app", "questions.js")
    js_path = os.path.normpath(js_path)
    with open(js_path, "w", encoding="utf-8") as f:
        f.write("const QUESTIONS = ")
        json.dump(app_questions, f, ensure_ascii=False, indent=2)
        f.write(";\n")

    # --- 統計出力 ---
    from collections import Counter
    cat_counter = Counter(q["category"] for q in app_questions)
    diff_counter = Counter(q["difficulty"] for q in app_questions)
    choices_count = sum(1 for q in app_questions if "choices" in q)
    exam_counter = Counter(q["exam"] for q in app_questions if q.get("exam"))
    revived_count = sum(1 for m in deduped if m.get("_source_file") == REVIVED_FILE)

    print("=== 統合結果 ===")
    print(f"総問題数: {len(app_questions)}")
    print(f"重複排除件数: {dup_count}")
    print(f"revived.json由来（復活）件数: {revived_count}")
    print(f"needs_review件数: {len(review_list)}")
    print("カテゴリ別内訳:")
    for cat, cnt in cat_counter.items():
        print(f"  {cat}: {cnt}")
    print("難易度別内訳:")
    for d in ("易", "普", "難"):
        print(f"  {d}: {diff_counter.get(d, 0)}")
    print("検定過去問 exam別内訳:")
    for exam in ("下忍試験", "中忍試験", "上忍試験"):
        print(f"  {exam}: {exam_counter.get(exam, 0)}")
    print(f"choices付き問題数: {choices_count}")
    print(f"出力: {master_path}")
    print(f"出力: {review_path}")
    print(f"出力: {js_path}")


if __name__ == "__main__":
    main()
