"""デイリーレビュー&改善提案(ゲート付き)。毎朝の答え合わせ完了後に実行。

1. 今回の答え合わせで確定した予想(=昨日分)の一覧と累計成績のサマリーを作る
2. Claude APIで「デイリー短評」を生成(日本語150字程度+英訳)。
   事実ベースで誇張・断定を避ける。確定0件の日はAI呼び出しをスキップ
   (追加のAI呼び出しは1日1回のみ。ウェブ検索なしの軽量呼び出し)
3. 改善提案はゲート付き: スポーツ×マーケット または 確率帯 の区分で
   「検証15件以上 かつ ROIが±15%超」の場合のみ、観察された傾向と提案を
   コード側で定型生成する(AI任せにせず決定論的・追加コストゼロ)。
   条件を満たさない日は「データ蓄積中(あとX件で最初の判定)」とだけ表示

重要: 提案の自動実装は絶対にしない。このモジュールは data/review.json への
保存・ダッシュボード表示・通知文の生成までで、config等への変更は一切行わない。
"""
import json
import os
import requests
from datetime import datetime, timezone, timedelta

from .config import MODEL

REVIEW_FILE = "data/review.json"
PHT = timezone(timedelta(hours=8))

GATE_MIN_N = 15        # 判定に必要な検証数(win+lose)
GATE_ROI_PCT = 15.0    # この%を超えるROIの偏りで提案を出す
MAX_PROPOSALS = 3      # 1日に表示する提案の上限(|ROI|降順)


# ---------- 改善提案(ゲート付き・コード生成) ----------

def _segments(stats: dict) -> list:
    """analytics()の集計から判定対象の区分を列挙:
    [(seg_ja, seg_en, n, roi)] 。スポーツ×マーケット + 確率帯"""
    from .dashboard import _mkt_en  # 市場名の英訳は表示層の既存マッピングを共用
    segs = []
    for sp in stats.get("mroi", []):
        for m in sp["markets"]:
            if m["roi"] is not None:
                segs.append((f'{sp["ja"]} × {m["market"]}',
                             f'{sp["en"]} × {_mkt_en(m["market"])}', m["n"], m["roi"]))
    for c in stats.get("calib", []):
        if c["roi"] is not None:
            segs.append((f'確率帯 {c["bin"]}', f'Prob band {c["bin"]}',
                         c["n"], c["roi"]))
    return segs


def build_proposals(stats: dict) -> dict:
    """ゲート判定。戻り値:
    {"proposals": [...], "status_ja": str, "status_en": str}
    proposalsが空の場合のみstatusに「データ蓄積中(あとX件)」等が入る"""
    segs = _segments(stats)
    hits = sorted((s for s in segs if s[2] >= GATE_MIN_N and abs(s[3]) > GATE_ROI_PCT),
                  key=lambda s: -abs(s[3]))[:MAX_PROPOSALS]

    proposals = []
    for seg_ja, seg_en, n, roi in hits:
        if roi < 0:
            sug_ja = ("この区分の表示格下げ(参考扱い)や、ブレンド重み"
                      "(WEIGHT_MARKET/WEIGHT_AI/WEIGHT_STAT)の見直しを検討")
            sug_en = ("Consider demoting this segment to reference-only display, "
                      "or revisiting the blend weights (WEIGHT_MARKET/AI/STAT)")
        else:
            sug_ja = ("好調な区分。ただし重み引き上げ等の強化は時期尚早で、"
                      "まずサンプルを増やして安定性を確認")
            sug_en = ("Performing well, but boosting weights would be premature — "
                      "keep accumulating samples to confirm stability")
        proposals.append({
            "segment_ja": seg_ja, "segment_en": seg_en, "n": n, "roi": round(roi, 1),
            "trend_ja": f"{seg_ja}は検証{n}件でROI{roi:+.1f}%と偏りが出ています",
            "trend_en": f"{seg_en}: ROI {roi:+.1f}% over {n} settled picks",
            "suggest_ja": sug_ja, "suggest_en": sug_en,
        })

    if proposals:
        return {"proposals": proposals, "status_ja": "", "status_en": ""}

    max_n = max((s[2] for s in segs), default=0)
    if max_n < GATE_MIN_N:
        remain = GATE_MIN_N - max_n
        return {"proposals": [],
                "status_ja": f"データ蓄積中(あと{remain}件で最初の判定)",
                "status_en": f"Accumulating data ({remain} more settled picks "
                             f"until the first evaluation)"}
    return {"proposals": [],
            "status_ja": f"±{GATE_ROI_PCT:.0f}%を超える偏りは現時点でありません(判定継続中)",
            "status_en": f"No segment deviates beyond ±{GATE_ROI_PCT:.0f}% ROI so far "
                         f"(evaluation continues)"}


# ---------- デイリー短評(AI・1日1回のみ) ----------

def _ai_comment(api_key: str, lines: list, y: dict, overall: dict) -> dict:
    """Claude APIで短評を生成(ウェブ検索なしの軽量呼び出し)。
    戻り値 {"ja": ..., "en": ...}。失敗時は例外を上げる(呼び出し側でフォールバック)"""
    roi_s = f'{overall["roi"]:+.1f}%' if overall.get("roi") is not None else "—"
    hit_s = f'{overall["hit"]:.1f}%' if overall.get("hit") is not None else "—"
    prompt = f"""あなたはスポーツベッティング予想の検証記録者です。以下は昨日確定した予想の結果です。

昨日の確定: {y['win']}勝{y['lose']}敗{y['push']}分 損益{y['profit']:+.2f}ユニット
{chr(10).join(lines)}

累計成績: 検証{overall.get('n', 0)}件 的中率{hit_s} ROI {roi_s}

この結果について「デイリー短評」を日本語150字程度で書いてください。
厳守事項:
- 昨日の結果の要点と、的中または外れの中で注目すべき1点(高オッズ的中、僅差の外れ、傾向など)に触れる
- 上の数字だけを使い、事実ベースで書く。数字の創作は禁止
- 誇張・断定・煽り(「絶対」「確実」「圧勝」等)を避け、淡々とした検証トーンで書く
- 投資勧誘的な表現は使わない

回答は次のJSONのみ:
{{"ja": "短評(150字程度)", "en": "Accurate English translation"}}"""
    body = {"model": MODEL, "max_tokens": 1200,
            "messages": [{"role": "user", "content": prompt}]}
    r = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={"x-api-key": api_key, "anthropic-version": "2023-06-01",
                 "content-type": "application/json"},
        json=body, timeout=120)
    r.raise_for_status()
    data = r.json()
    text = "\n".join(b.get("text", "") for b in data.get("content", [])
                     if b.get("type") == "text")
    clean = text.replace("```json", "").replace("```", "").strip()
    start = clean.find("{")
    return json.loads(clean[start: clean.rfind("}") + 1])


def _result_mark(r) -> str:
    return {"win": "✅的中", "lose": "❌外れ", "push": "➖返金"}.get(r["result"], "?")


def build_review(rows: list, newly_settled: list, ai_key: str, now=None) -> dict:
    """レビューを構築して返す(保存はsave()で)。
    rows: 全履歴 / newly_settled: 今回の答え合わせで確定した行(=昨日分)。
    戻り値dictの"ai_called"は費用レポート用(AI呼び出しを行ったか)"""
    from .main import analytics  # 循環インポート回避(main→review→main)のため遅延
    now = now or datetime.now(timezone.utc)
    stats = analytics(rows)
    y = {"n": len(newly_settled),
         "win": sum(1 for r in newly_settled if r["result"] == "win"),
         "lose": sum(1 for r in newly_settled if r["result"] == "lose"),
         "push": sum(1 for r in newly_settled if r["result"] == "push"),
         "profit": sum(float(r["profit"] or 0) for r in newly_settled)}

    review = {"date": now.astimezone(PHT).strftime("%Y-%m-%d"),
              "yesterday": y, "ai_called": False,
              **build_proposals(stats)}

    if not newly_settled:
        # 確定0件の日はAI呼び出しをスキップ(コストガード)
        review["comment_ja"] = "昨日は確定した予想なし"
        review["comment_en"] = "No picks settled yesterday"
        return review

    lines = [f"- {r['match']} / {r['market']}: {r['pick']} → {_result_mark(r)} "
             f"({float(r['profit'] or 0):+.2f}u, オッズ{r['odds']})"
             for r in newly_settled[:40]]   # プロンプト肥大防止
    try:
        c = _ai_comment(ai_key, lines, y, stats["overall"])
        review["comment_ja"] = (c.get("ja") or "").strip()
        review["comment_en"] = (c.get("en") or "").strip()
        review["ai_called"] = True
    except Exception as e:  # AI失敗でもレビュー自体は成立させる(集計は事実なので)
        print(f"[warn] review AI comment failed: {e}")
        push_ja = f"{y['push']}分" if y["push"] else ""
        push_en = f"-{y['push']}P" if y["push"] else ""
        review["comment_ja"] = (f"昨日は{y['win']}勝{y['lose']}敗{push_ja}、"
                                f"損益{y['profit']:+.2f}ユニットでした。")
        review["comment_en"] = (f"Yesterday: {y['win']}W-{y['lose']}L{push_en}, "
                                f"{y['profit']:+.2f} units.")
    return review


def save(review: dict, path: str = REVIEW_FILE):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(review, f, ensure_ascii=False, indent=1)
        f.write("\n")


def load(path: str = REVIEW_FILE) -> dict:
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as f:
        return json.load(f) or {}


def notify_text(review: dict) -> str:
    """Slack/Discord通知用の短評テキスト(通知設定がある場合にnotify.postへ渡す)"""
    if not review:
        return ""
    y = review.get("yesterday", {})
    lines = ["📝 デイリーレビュー"]
    if y.get("n"):
        rec = f"昨日: {y['win']}勝{y['lose']}敗" + (f"{y['push']}分" if y.get("push") else "")
        lines.append(f"{rec} / 損益 {y['profit']:+.2f}u")
    lines.append(review.get("comment_ja", ""))
    for p in review.get("proposals", []):
        lines.append(f"💡 改善提案: {p['trend_ja']} → {p['suggest_ja']}")
    if review.get("proposals"):
        lines.append("(提案は自動適用されません。表示と通知のみ)")
    elif review.get("status_ja"):
        lines.append(review["status_ja"])
    return "\n".join(s for s in lines if s)
