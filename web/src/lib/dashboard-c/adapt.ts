/**
 * 백엔드 snapshot/candles/news → option-c 뷰모델 변환 어댑터.
 * 모든 필드는 null-safe. 컴포넌트는 여기 결과(DashboardView 등)만 소비한다.
 */

import type {
  DashboardSnapshot,
  CandleData,
  SignalInfo,
} from "@/lib/types";
import { C, CYCLE_STAGES, sourceLabel, type SignalType } from "./tokens";
import { wonToKoreanAmount, won, dec1, buildChange } from "./format";
import { buildHeadline, buildReasonCards, buildEvidence } from "./copy";
import type {
  DashboardView,
  TickerOption,
  ReasonCard,
  ChartView,
  ChartMarker,
  FinanceCard,
  NewsView,
  NewsArticleView,
  HorizonRow,
  AiReasoning,
} from "./types";

/** 종목명 첫 글자(영문은 앞 2자)로 로고 이니셜 생성. */
export function logoFromName(name: string): string {
  if (!name) return "·";
  const trimmed = name.trim();
  // 영문이면 앞 2글자 대문자, 한글이면 첫 글자
  if (/^[A-Za-z]/.test(trimmed)) return trimmed.slice(0, 2).toUpperCase();
  return trimmed.slice(0, 1);
}

/** /api/stocks → TickerOption[] (활성 종목 우선, 비활성은 "준비 중"). */
export function adaptTickers(
  rows: { stock_code: string; name: string; is_active: number }[]
): TickerOption[] {
  return rows.map((r) => ({
    ticker: r.stock_code,
    name: r.name,
    logo: logoFromName(r.name),
    ready: r.is_active === 1,
  }));
}

const VALID_SIGNALS: ReadonlySet<string> = new Set(["BUY", "SELL", "HOLD"]);
function signalType(sig: SignalInfo | null): SignalType | null {
  if (!sig) return null;
  return VALID_SIGNALS.has(sig.signal_type) ? (sig.signal_type as SignalType) : null;
}

/** DART 종목명 검색 URL (corp_code 직링크는 후속 task — MVP는 검색 진입). */
export function dartSearchUrl(name: string): string {
  return `https://dart.fss.or.kr/dsab007/main.do?option=corp&textCrpNm=${encodeURIComponent(name)}`;
}

/** signaled_at(ISO) → candles 시계열에서 가장 가까운 index. */
function nearestCandleIdx(candles: CandleData[], isoDate: string): number {
  const target = isoDate.substring(0, 10);
  // 정확 매칭 우선
  const exact = candles.findIndex((c) => c.time.substring(0, 10) === target);
  if (exact >= 0) return exact;
  // 가장 가까운 날짜
  const t = new Date(target).getTime();
  if (Number.isNaN(t) || candles.length === 0) return candles.length - 1;
  let best = candles.length - 1;
  let bestDiff = Number.POSITIVE_INFINITY;
  candles.forEach((c, i) => {
    const ct = new Date(c.time.substring(0, 10)).getTime();
    const diff = Math.abs(ct - t);
    if (diff < bestDiff) {
      bestDiff = diff;
      best = i;
    }
  });
  return best;
}

const SHORT_TIP: Record<SignalType, string> = {
  BUY: "이 시점에 매수 신호가 켜졌어요.",
  SELL: "이 시점에 매도 신호가 나왔어요.",
  HOLD: "이 시점에 관망 신호로 바뀌었어요.",
};

/** 차트 뷰: 종가 라인 + 절제된 시그널 마커(가장 최근 관점 전환 1개). */
function adaptChart(
  candles: CandleData[],
  signals: DashboardSnapshot["signals"]
): ChartView | null {
  if (!candles.length) return null;
  // 최근 30거래일
  const recent = candles.slice(-30);
  const offset = candles.length - recent.length;
  const priceTrend = recent.map((c) => c.close);

  // 마커 후보: 각 관점의 최신 신호 중 signaled_at이 가장 최근인 1개만 (절제)
  const candidates: { sig: SignalInfo; type: SignalType }[] = [];
  (["short", "mid", "long"] as const).forEach((k) => {
    const sig = signals[k];
    const t = signalType(sig);
    if (sig && t && sig.signaled_at) candidates.push({ sig, type: t });
  });
  const markers: ChartMarker[] = [];
  if (candidates.length) {
    candidates.sort(
      (a, b) => new Date(b.sig.signaled_at).getTime() - new Date(a.sig.signaled_at).getTime()
    );
    const top = candidates[0];
    const absIdx = nearestCandleIdx(candles, top.sig.signaled_at);
    const relIdx = absIdx - offset;
    if (relIdx >= 0 && relIdx < recent.length) {
      markers.push({
        idx: relIdx,
        signal: top.type,
        short: `${top.type === "BUY" ? "매수" : top.type === "SELL" ? "매도" : "관망"} 신호`,
        tip: SHORT_TIP[top.type],
      });
    }
  }

  return { priceTrend, markers, barCount: recent.length };
}

function adaptHorizons(signals: DashboardSnapshot["signals"]): HorizonRow[] {
  return [
    { when: "며칠 ~ 2주", sub: "단기", signal: signalType(signals.short) },
    { when: "한두 달", sub: "중기", signal: signalType(signals.mid) },
    { when: "1년 이상", sub: "장기", signal: signalType(signals.long) },
  ];
}

function adaptFinanceCards(f: DashboardSnapshot["financials"]): FinanceCard[] {
  const cards: FinanceCard[] = [];
  if (f.revenue != null)
    cards.push({
      emoji: "🏭",
      tint: C.amberTint,
      accent: C.amber,
      label: "매출액",
      value: wonToKoreanAmount(f.revenue),
      easy: "회사가 1년에 벌어들인 총 매출이에요.",
    });
  if (f.operating_profit != null)
    cards.push({
      emoji: "💰",
      tint: C.emeraldTint,
      accent: C.emerald,
      label: "영업이익",
      value: wonToKoreanAmount(f.operating_profit),
      easy: "매출에서 비용을 빼고 실제로 남긴 이익이에요.",
    });
  if (f.per != null)
    cards.push({
      emoji: "⚖️",
      tint: C.roseTint,
      accent: C.rose,
      label: "PER",
      value: dec1(f.per),
      easy: "주가가 1년 이익의 몇 배인지 — 낮을수록 저렴해요.",
    });
  if (f.pbr != null)
    cards.push({
      emoji: "🏦",
      tint: C.amberTint,
      accent: C.amber,
      label: "PBR",
      value: dec1(f.pbr),
      easy: "자산 대비 주가 수준 — 1보다 크면 자산보다 비싸게 거래돼요.",
    });
  if (f.eps != null)
    cards.push({
      emoji: "🪙",
      tint: C.emeraldTint,
      accent: C.emerald,
      label: "EPS",
      value: won(f.eps) + "원",
      easy: "주식 1주가 1년에 버는 이익이에요.",
    });
  return cards;
}

/** snapshot(+candles) → DashboardView. snapshot이 null이면 ready=false 뷰. */
export function adaptSnapshot(
  snapshot: DashboardSnapshot | null,
  candles: CandleData[],
  fallbackName: string
): DashboardView {
  const name = snapshot?.stock.name ?? fallbackName;
  const ticker = snapshot?.stock.stock_code ?? "";
  const close = snapshot?.price.close ?? null;
  const mid = snapshot ? signalType(snapshot.signals.mid) : null;

  // ready 판정: 핵심 필드(종가 + 중기 신호) 존재
  const ready = close != null && mid != null;

  const score = snapshot?.sentiment.score ?? null;
  const pos = snapshot?.price.band_pos_pct ?? null;
  const expensive = pos != null && pos >= 70;

  const topKeywords: string[] =
    snapshot?.sentiment.top_keywords
      ?.filter((k) => k.weight > 0)
      .map((k) => k.word) ?? [];

  const reasonCards: ReasonCard[] = buildReasonCards(score, pos, topKeywords[0] ?? null);

  const headline = mid ? buildHeadline(mid, expensive, score) : null;

  // 분위기 vs 가격 비교
  const sentiPct = score != null ? (score + 100) / 2 : 0;
  const gap =
    score != null && pos != null
      ? {
          sentiPct,
          sentiScore: score,
          pricePct: pos,
          expensive,
          verdict:
            Math.abs(sentiPct - pos) >= 18
              ? { color: C.amber, tint: C.amberTint, text: "그래서 지금은 신중!" }
              : { color: C.emerald, tint: C.emeraldTint, text: "분위기와 가격이 균형!" },
        }
      : null;

  // 사이클
  const cycle = snapshot?.cycle
    ? {
        phase: CYCLE_STAGES.some((s) => s.key === snapshot.cycle?.phase)
          ? snapshot.cycle.phase
          : "EARLY_CYCLE",
        score: Math.max(0, Math.min(100, snapshot.cycle.cycle_score)),
      }
    : null;

  const evidence = snapshot
    ? buildEvidence({
        score,
        pos,
        per: snapshot.financials.per,
        pbr: snapshot.financials.pbr,
        bandLow: snapshot.price.band_low,
        bandHigh: snapshot.price.band_high,
        mid,
        short: signalType(snapshot.signals.short),
        long: signalType(snapshot.signals.long),
        topKeywords,
      })
    : [];

  const financeBand =
    snapshot && close != null && snapshot.price.band_low != null && snapshot.price.band_high != null && pos != null
      ? {
          low: snapshot.price.band_low,
          high: snapshot.price.band_high,
          current: close,
          pos,
        }
      : null;

  const HZ: Array<["short" | "mid" | "long", string]> = [
    ["short", "단기"],
    ["mid", "중기"],
    ["long", "장기"],
  ];
  const aiReasonings: AiReasoning[] = HZ.flatMap(([h, label]) => {
    const r = snapshot?.reasonings?.[h];
    return r && !r.is_fallback && r.reasoning?.trim()
      ? [{ horizon: h, label, text: r.reasoning.trim() }]
      : [];
  });

  return {
    ready,
    header: {
      ticker,
      name,
      logo: logoFromName(name),
      price: close != null ? won(close) : "—",
      change: snapshot ? buildChange(close, snapshot.price.open) : null,
    },
    sentiScore: score,
    bandPos: pos,
    expensive,
    midSignal: mid,
    headline,
    reasonCards,
    gap,
    cycle,
    horizons: snapshot ? adaptHorizons(snapshot.signals) : [],
    chart: adaptChart(candles, snapshot?.signals ?? { short: null, mid: null, long: null }),
    evidence,
    aiReasonings,
    financeCards: snapshot ? adaptFinanceCards(snapshot.financials) : [],
    financeBand,
    financeName: name,
    dartUrl: dartSearchUrl(name),
  };
}

/** /api/news/{code} 응답 → NewsView. (백엔드 완료 후 연결) */
export interface NewsApiResponse {
  stock_code: string;
  analyzed_count: number;
  items: {
    title: string;
    summary: string;
    source: string;
    url: string;
    published_at: string;
    sentiment_direction: "positive" | "negative" | "neutral";
    keywords: string[];
  }[];
}

export function adaptNews(
  res: NewsApiResponse | undefined,
  sentiScore: number | null,
  relativeTime: (iso: string) => string
): NewsView {
  if (!res) return { analyzedCount: null, sentiScore, articles: [] };
  const articles: NewsArticleView[] = (res.items ?? []).map((n) => ({
    title: n.title,
    summary: n.summary,
    // raw 출처(naver_news/google_rss) → 한글 라벨, 미매핑은 원문 유지
    source: sourceLabel(n.source),
    url: n.url,
    when: relativeTime(n.published_at),
    sentiment: n.sentiment_direction,
    keywords: n.keywords ?? [],
  }));
  return { analyzedCount: res.analyzed_count, sentiScore, articles };
}
