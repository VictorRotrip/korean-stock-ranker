// =============================================================================
// Mock Korean Stock Data
// =============================================================================
// Realistic mock data for 60 Korean stocks spanning KOSPI and KOSDAQ.
// Includes price data, financial statements, and short selling data.
// This allows the full ranking engine to run without any external data source.
//
// NOTE: These numbers are illustrative approximations, not real financial data.
// Real data will come from pykrx, DART, KRX in later milestones.
// =============================================================================

import type {
  Stock,
  DailyPrice,
  FinancialStatement,
  ShortSellingData,
  Market,
} from "@/types";

// ---------------------------------------------------------------------------
// Helper: seeded pseudo-random number generator for reproducibility
// ---------------------------------------------------------------------------
function seededRandom(seed: number): () => number {
  let s = seed;
  return () => {
    s = (s * 16807 + 0) % 2147483647;
    return s / 2147483647;
  };
}

const rand = seededRandom(42);

function randBetween(min: number, max: number): number {
  return min + rand() * (max - min);
}

function randInt(min: number, max: number): number {
  return Math.floor(randBetween(min, max + 1));
}

// ---------------------------------------------------------------------------
// Stock Universe
// ---------------------------------------------------------------------------

interface MockStockSeed {
  ticker: string;
  name: string;
  nameEn: string;
  market: Market;
  sector: string;
  industry: string;
  approxMarketCapBillions: number; // approximate market cap in 억원
  isFinancial?: boolean;
}

const stockSeeds: MockStockSeed[] = [
  // --- KOSPI Large Caps ---
  { ticker: "005930", name: "삼성전자", nameEn: "Samsung Electronics", market: "KOSPI", sector: "IT", industry: "반도체", approxMarketCapBillions: 4000000 },
  { ticker: "000660", name: "SK하이닉스", nameEn: "SK Hynix", market: "KOSPI", sector: "IT", industry: "반도체", approxMarketCapBillions: 1200000 },
  { ticker: "373220", name: "LG에너지솔루션", nameEn: "LG Energy Solution", market: "KOSPI", sector: "IT", industry: "2차전지", approxMarketCapBillions: 900000 },
  { ticker: "207940", name: "삼성바이오로직스", nameEn: "Samsung Biologics", market: "KOSPI", sector: "헬스케어", industry: "바이오", approxMarketCapBillions: 550000 },
  { ticker: "005490", name: "POSCO홀딩스", nameEn: "POSCO Holdings", market: "KOSPI", sector: "소재", industry: "철강", approxMarketCapBillions: 350000 },
  { ticker: "006400", name: "삼성SDI", nameEn: "Samsung SDI", market: "KOSPI", sector: "IT", industry: "2차전지", approxMarketCapBillions: 300000 },
  { ticker: "051910", name: "LG화학", nameEn: "LG Chem", market: "KOSPI", sector: "소재", industry: "화학", approxMarketCapBillions: 280000 },
  { ticker: "035420", name: "NAVER", nameEn: "NAVER", market: "KOSPI", sector: "커뮤니케이션", industry: "인터넷", approxMarketCapBillions: 350000 },
  { ticker: "000270", name: "기아", nameEn: "Kia", market: "KOSPI", sector: "경기소비재", industry: "자동차", approxMarketCapBillions: 340000 },
  { ticker: "005380", name: "현대자동차", nameEn: "Hyundai Motor", market: "KOSPI", sector: "경기소비재", industry: "자동차", approxMarketCapBillions: 450000 },
  { ticker: "035720", name: "카카오", nameEn: "Kakao", market: "KOSPI", sector: "커뮤니케이션", industry: "인터넷", approxMarketCapBillions: 200000 },
  { ticker: "068270", name: "셀트리온", nameEn: "Celltrion", market: "KOSPI", sector: "헬스케어", industry: "바이오", approxMarketCapBillions: 250000 },
  { ticker: "028260", name: "삼성물산", nameEn: "Samsung C&T", market: "KOSPI", sector: "산업재", industry: "건설", approxMarketCapBillions: 250000 },
  { ticker: "105560", name: "KB금융", nameEn: "KB Financial Group", market: "KOSPI", sector: "금융", industry: "은행", approxMarketCapBillions: 260000, isFinancial: true },
  { ticker: "055550", name: "신한지주", nameEn: "Shinhan Financial Group", market: "KOSPI", sector: "금융", industry: "은행", approxMarketCapBillions: 220000, isFinancial: true },
  { ticker: "086790", name: "하나금융지주", nameEn: "Hana Financial Group", market: "KOSPI", sector: "금융", industry: "은행", approxMarketCapBillions: 180000, isFinancial: true },
  { ticker: "066570", name: "LG전자", nameEn: "LG Electronics", market: "KOSPI", sector: "IT", industry: "가전", approxMarketCapBillions: 150000 },
  { ticker: "003670", name: "포스코퓨처엠", nameEn: "POSCO Future M", market: "KOSPI", sector: "소재", industry: "2차전지소재", approxMarketCapBillions: 120000 },
  { ticker: "012330", name: "현대모비스", nameEn: "Hyundai Mobis", market: "KOSPI", sector: "경기소비재", industry: "자동차부품", approxMarketCapBillions: 200000 },
  { ticker: "034730", name: "SK", nameEn: "SK Inc", market: "KOSPI", sector: "산업재", industry: "지주", approxMarketCapBillions: 140000 },
  // --- KOSPI Mid Caps ---
  { ticker: "009150", name: "삼성전기", nameEn: "Samsung Electro-Mechanics", market: "KOSPI", sector: "IT", industry: "전자부품", approxMarketCapBillions: 100000 },
  { ticker: "018260", name: "삼성에스디에스", nameEn: "Samsung SDS", market: "KOSPI", sector: "IT", industry: "IT서비스", approxMarketCapBillions: 95000 },
  { ticker: "010130", name: "고려아연", nameEn: "Korea Zinc", market: "KOSPI", sector: "소재", industry: "비철금속", approxMarketCapBillions: 90000 },
  { ticker: "003490", name: "대한항공", nameEn: "Korean Air", market: "KOSPI", sector: "산업재", industry: "항공", approxMarketCapBillions: 85000 },
  { ticker: "032830", name: "삼성생명", nameEn: "Samsung Life Insurance", market: "KOSPI", sector: "금융", industry: "보험", approxMarketCapBillions: 80000, isFinancial: true },
  { ticker: "030200", name: "KT", nameEn: "KT Corporation", market: "KOSPI", sector: "커뮤니케이션", industry: "통신", approxMarketCapBillions: 75000 },
  { ticker: "017670", name: "SK텔레콤", nameEn: "SK Telecom", market: "KOSPI", sector: "커뮤니케이션", industry: "통신", approxMarketCapBillions: 110000 },
  { ticker: "096770", name: "SK이노베이션", nameEn: "SK Innovation", market: "KOSPI", sector: "에너지", industry: "정유", approxMarketCapBillions: 70000 },
  { ticker: "010950", name: "S-Oil", nameEn: "S-Oil", market: "KOSPI", sector: "에너지", industry: "정유", approxMarketCapBillions: 65000 },
  { ticker: "011170", name: "롯데케미칼", nameEn: "Lotte Chemical", market: "KOSPI", sector: "소재", industry: "화학", approxMarketCapBillions: 40000 },
  { ticker: "000810", name: "삼성화재", nameEn: "Samsung Fire & Marine", market: "KOSPI", sector: "금융", industry: "보험", approxMarketCapBillions: 60000, isFinancial: true },
  { ticker: "033780", name: "KT&G", nameEn: "KT&G", market: "KOSPI", sector: "필수소비재", industry: "담배", approxMarketCapBillions: 95000 },
  { ticker: "004020", name: "현대제철", nameEn: "Hyundai Steel", market: "KOSPI", sector: "소재", industry: "철강", approxMarketCapBillions: 35000 },
  { ticker: "015760", name: "한국전력", nameEn: "KEPCO", market: "KOSPI", sector: "유틸리티", industry: "전력", approxMarketCapBillions: 130000 },
  { ticker: "036570", name: "엔씨소프트", nameEn: "NCSoft", market: "KOSPI", sector: "커뮤니케이션", industry: "게임", approxMarketCapBillions: 45000 },
  { ticker: "090430", name: "아모레퍼시픽", nameEn: "Amorepacific", market: "KOSPI", sector: "필수소비재", industry: "화장품", approxMarketCapBillions: 50000 },
  { ticker: "034020", name: "두산에너빌리티", nameEn: "Doosan Enerbility", market: "KOSPI", sector: "산업재", industry: "중공업", approxMarketCapBillions: 55000 },
  { ticker: "011790", name: "SKC", nameEn: "SKC", market: "KOSPI", sector: "소재", industry: "화학", approxMarketCapBillions: 25000 },
  { ticker: "047050", name: "포스코인터내셔널", nameEn: "POSCO International", market: "KOSPI", sector: "산업재", industry: "무역", approxMarketCapBillions: 45000 },
  { ticker: "003550", name: "LG", nameEn: "LG Corp", market: "KOSPI", sector: "산업재", industry: "지주", approxMarketCapBillions: 100000 },
  // --- KOSDAQ ---
  { ticker: "247540", name: "에코프로비엠", nameEn: "EcoPro BM", market: "KOSDAQ", sector: "소재", industry: "2차전지소재", approxMarketCapBillions: 150000 },
  { ticker: "086520", name: "에코프로", nameEn: "EcoPro", market: "KOSDAQ", sector: "소재", industry: "2차전지소재", approxMarketCapBillions: 100000 },
  { ticker: "196170", name: "알테오젠", nameEn: "Alteogen", market: "KOSDAQ", sector: "헬스케어", industry: "바이오", approxMarketCapBillions: 120000 },
  { ticker: "403870", name: "HPSP", nameEn: "HPSP", market: "KOSDAQ", sector: "IT", industry: "반도체장비", approxMarketCapBillions: 50000 },
  { ticker: "293490", name: "카카오게임즈", nameEn: "Kakao Games", market: "KOSDAQ", sector: "커뮤니케이션", industry: "게임", approxMarketCapBillions: 25000 },
  { ticker: "263750", name: "펄어비스", nameEn: "Pearl Abyss", market: "KOSDAQ", sector: "커뮤니케이션", industry: "게임", approxMarketCapBillions: 20000 },
  { ticker: "145020", name: "휴젤", nameEn: "Hugel", market: "KOSDAQ", sector: "헬스케어", industry: "의료기기", approxMarketCapBillions: 30000 },
  { ticker: "112040", name: "위메이드", nameEn: "WeMade", market: "KOSDAQ", sector: "커뮤니케이션", industry: "게임", approxMarketCapBillions: 15000 },
  { ticker: "357780", name: "솔브레인", nameEn: "Soulbrain", market: "KOSDAQ", sector: "소재", industry: "반도체소재", approxMarketCapBillions: 35000 },
  { ticker: "058470", name: "리노공업", nameEn: "LEENO Industrial", market: "KOSDAQ", sector: "IT", industry: "반도체장비", approxMarketCapBillions: 30000 },
  { ticker: "041510", name: "에스엠", nameEn: "SM Entertainment", market: "KOSDAQ", sector: "커뮤니케이션", industry: "엔터테인먼트", approxMarketCapBillions: 25000 },
  { ticker: "328130", name: "루닛", nameEn: "Lunit", market: "KOSDAQ", sector: "헬스케어", industry: "AI의료", approxMarketCapBillions: 28000 },
  { ticker: "039030", name: "이오테크닉스", nameEn: "EO Technics", market: "KOSDAQ", sector: "IT", industry: "레이저장비", approxMarketCapBillions: 22000 },
  { ticker: "298050", name: "효성첨단소재", nameEn: "Hyosung Advanced Materials", market: "KOSDAQ", sector: "소재", industry: "산업소재", approxMarketCapBillions: 18000 },
  { ticker: "035900", name: "JYP Ent.", nameEn: "JYP Entertainment", market: "KOSDAQ", sector: "커뮤니케이션", industry: "엔터테인먼트", approxMarketCapBillions: 32000 },
  { ticker: "007390", name: "네이처셀", nameEn: "NaturalCell", market: "KOSDAQ", sector: "헬스케어", industry: "바이오", approxMarketCapBillions: 5000 },
  { ticker: "095340", name: "ISC", nameEn: "ISC", market: "KOSDAQ", sector: "IT", industry: "반도체부품", approxMarketCapBillions: 12000 },
  { ticker: "352820", name: "하이브", nameEn: "HYBE", market: "KOSPI", sector: "커뮤니케이션", industry: "엔터테인먼트", approxMarketCapBillions: 80000 },
  { ticker: "377300", name: "카카오페이", nameEn: "KakaoPay", market: "KOSPI", sector: "금융", industry: "핀테크", approxMarketCapBillions: 45000, isFinancial: true },
  { ticker: "316140", name: "우리금융지주", nameEn: "Woori Financial Group", market: "KOSPI", sector: "금융", industry: "은행", approxMarketCapBillions: 110000, isFinancial: true },
];

// ---------------------------------------------------------------------------
// Generate Stocks
// ---------------------------------------------------------------------------

export function getMockStocks(): Stock[] {
  return stockSeeds.map((s) => ({
    ticker: s.ticker,
    name: s.name,
    nameEn: s.nameEn,
    market: s.market,
    sector: s.sector,
    industry: s.industry,
    listingDate: "2000-01-04",
    isActive: true,
    isSpac: false,
    isPreferred: false,
    isEtf: false,
    isReit: false,
    isFinancial: s.isFinancial ?? false,
    isHolding: false,
  }));
}

// ---------------------------------------------------------------------------
// Generate Financial Statements
// ---------------------------------------------------------------------------

/**
 * For each stock, generate 4 years of annual financials + 4 quarters of recent data.
 * Values are roughly calibrated to the stock's market cap tier.
 */
export function getMockFinancials(): FinancialStatement[] {
  const results: FinancialStatement[] = [];

  for (const seed of stockSeeds) {
    const r = seededRandom(hashCode(seed.ticker));
    const rr = () => r();
    const mcap = seed.approxMarketCapBillions * 1e8; // convert 억원 to KRW

    // Base revenue is roughly 20-60% of market cap for non-financial companies
    const revMultiplier = seed.isFinancial ? 0.08 : (0.2 + rr() * 0.4);
    const baseRevenue = mcap * revMultiplier;

    // Generate annual data for 2020-2023
    for (let year = 2020; year <= 2023; year++) {
      const growthFactor = 1 + (year - 2020) * (0.03 + rr() * 0.12);
      const revenue = baseRevenue * growthFactor * (0.9 + rr() * 0.2);

      const grossMargin = seed.isFinancial ? 0.9 : (0.15 + rr() * 0.45);
      const opMargin = seed.isFinancial ? (0.15 + rr() * 0.2) : (0.03 + rr() * 0.2);
      const netMargin = opMargin * (0.6 + rr() * 0.3);

      const grossProfit = revenue * grossMargin;
      const operatingIncome = revenue * opMargin;
      const netIncome = revenue * netMargin;
      const ebitda = operatingIncome * (1.15 + rr() * 0.2);

      const totalAssets = mcap * (0.8 + rr() * 1.5);
      const totalEquity = totalAssets * (0.3 + rr() * 0.4);
      const totalLiabilities = totalAssets - totalEquity;
      const totalDebt = totalLiabilities * (0.3 + rr() * 0.5);

      const shares = mcap / (30000 + rr() * 200000); // rough share count
      const eps = netIncome / shares;
      const bvps = totalEquity / shares;

      const ocf = netIncome * (1 + rr() * 0.5);
      const capex = revenue * (0.02 + rr() * 0.1);
      const fcf = ocf - capex;
      const divPaid = netIncome > 0 ? netIncome * (0.1 + rr() * 0.3) : 0;

      results.push({
        ticker: seed.ticker,
        periodEnd: `${year}-12-31`,
        periodType: "annual",
        // Simulate DART filing ~90 days after period end
        filingDate: `${year + 1}-03-${15 + Math.floor(rr() * 16)}`,
        revenue: Math.round(revenue),
        costOfRevenue: Math.round(revenue - grossProfit),
        grossProfit: Math.round(grossProfit),
        operatingIncome: Math.round(operatingIncome),
        netIncome: Math.round(netIncome),
        eps: Math.round(eps),
        totalAssets: Math.round(totalAssets),
        totalLiabilities: Math.round(totalLiabilities),
        totalEquity: Math.round(totalEquity),
        bookValuePerShare: Math.round(bvps),
        currentAssets: Math.round(totalAssets * (0.2 + rr() * 0.3)),
        currentLiabilities: Math.round(totalLiabilities * (0.3 + rr() * 0.3)),
        cash: Math.round(totalAssets * (0.05 + rr() * 0.15)),
        shortTermDebt: Math.round(totalDebt * 0.3),
        longTermDebt: Math.round(totalDebt * 0.7),
        totalDebt: Math.round(totalDebt),
        operatingCashFlow: Math.round(ocf),
        capitalExpenditure: Math.round(capex),
        freeCashFlow: Math.round(fcf),
        dividendsPaid: Math.round(divPaid),
        ebitda: Math.round(ebitda),
        interestExpense: Math.round(totalDebt * (0.02 + rr() * 0.04)),
        depreciation: Math.round(ebitda - operatingIncome),
        sharesOutstanding: Math.round(shares),
      });
    }
  }

  return results;
}

// ---------------------------------------------------------------------------
// Generate Daily Prices (last 252 trading days ≈ 1 year)
// ---------------------------------------------------------------------------

export function getMockPrices(): DailyPrice[] {
  const results: DailyPrice[] = [];
  const tradingDays = generateTradingDays(252);

  for (const seed of stockSeeds) {
    const r = seededRandom(hashCode(seed.ticker) + 1000);
    const rr = () => r();

    // Derive a base price from market cap
    const shares = seed.approxMarketCapBillions * 1e8 / (30000 + rr() * 200000);
    let price = seed.approxMarketCapBillions * 1e8 / shares;

    for (const day of tradingDays) {
      // Random walk with slight drift
      const dailyReturn = (rr() - 0.48) * 0.04; // slight positive drift
      price = price * (1 + dailyReturn);
      price = Math.max(price, 100); // floor at 100 KRW

      const volume = Math.round(shares * (0.001 + rr() * 0.02));
      const tradingValue = Math.round(price * volume);

      results.push({
        ticker: seed.ticker,
        date: day,
        open: Math.round(price * (1 + (rr() - 0.5) * 0.02)),
        high: Math.round(price * (1 + rr() * 0.03)),
        low: Math.round(price * (1 - rr() * 0.03)),
        close: Math.round(price),
        volume,
        tradingValue,
        marketCap: Math.round(price * shares),
        sharesOutstanding: Math.round(shares),
      });
    }
  }

  return results;
}

// ---------------------------------------------------------------------------
// Generate Short Selling Data (last 60 trading days)
// ---------------------------------------------------------------------------

export function getMockShortSelling(): ShortSellingData[] {
  const results: ShortSellingData[] = [];
  const tradingDays = generateTradingDays(60);

  for (const seed of stockSeeds) {
    const r = seededRandom(hashCode(seed.ticker) + 2000);
    const rr = () => r();

    const baseShortRatio = 0.01 + rr() * 0.08; // 1-9% base short ratio

    for (const day of tradingDays) {
      const shortRatio = baseShortRatio * (0.7 + rr() * 0.6);
      const shares = seed.approxMarketCapBillions * 1e8 / 50000;
      const volume = shares * (0.001 + rr() * 0.02);
      const shortVolume = Math.round(volume * shortRatio);
      const price = seed.approxMarketCapBillions * 1e8 / shares;

      results.push({
        ticker: seed.ticker,
        date: day,
        shortVolume,
        shortValue: Math.round(shortVolume * price),
        shortBalance: Math.round(shares * baseShortRatio * (0.8 + rr() * 0.4)),
        shortBalanceValue: Math.round(shares * baseShortRatio * price * (0.8 + rr() * 0.4)),
        shortRatio: Math.round(shortRatio * 10000) / 100, // percentage with 2 decimals
      });
    }
  }

  return results;
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function hashCode(str: string): number {
  let hash = 0;
  for (let i = 0; i < str.length; i++) {
    const char = str.charCodeAt(i);
    hash = ((hash << 5) - hash) + char;
    hash = hash & hash; // Convert to 32-bit integer
  }
  return Math.abs(hash);
}

/**
 * Generate N trading days ending at "today" (2024-12-20 for reproducibility).
 * Skips weekends.
 */
function generateTradingDays(count: number): string[] {
  const days: string[] = [];
  const end = new Date("2024-12-20");
  let current = new Date(end);

  while (days.length < count) {
    const dow = current.getDay();
    if (dow !== 0 && dow !== 6) {
      days.unshift(current.toISOString().split("T")[0]);
    }
    current.setDate(current.getDate() - 1);
  }

  return days;
}

// ---------------------------------------------------------------------------
// Prebuilt Data Access (lazy-initialized singletons)
// ---------------------------------------------------------------------------

let _stocks: Stock[] | null = null;
let _financials: FinancialStatement[] | null = null;
let _prices: DailyPrice[] | null = null;
let _shortSelling: ShortSellingData[] | null = null;

export function getStocks(): Stock[] {
  if (!_stocks) _stocks = getMockStocks();
  return _stocks;
}

export function getFinancials(): FinancialStatement[] {
  if (!_financials) _financials = getMockFinancials();
  return _financials;
}

export function getPrices(): DailyPrice[] {
  if (!_prices) _prices = getMockPrices();
  return _prices;
}

export function getShortSellingData(): ShortSellingData[] {
  if (!_shortSelling) _shortSelling = getMockShortSelling();
  return _shortSelling;
}

/**
 * Get the latest price for each stock.
 */
export function getLatestPrices(): Map<string, DailyPrice> {
  const prices = getPrices();
  const latest = new Map<string, DailyPrice>();

  for (const p of prices) {
    const existing = latest.get(p.ticker);
    if (!existing || p.date > existing.date) {
      latest.set(p.ticker, p);
    }
  }

  return latest;
}

/**
 * Get the most recent annual financial statement for each stock,
 * respecting point-in-time: only returns filings with filingDate <= asOfDate.
 */
export function getLatestFinancials(asOfDate: string): Map<string, FinancialStatement> {
  const financials = getFinancials();
  const latest = new Map<string, FinancialStatement>();

  for (const f of financials) {
    if (f.periodType !== "annual") continue;
    // Point-in-time: only use data that was available by asOfDate
    if (f.filingDate > asOfDate) continue;

    const existing = latest.get(f.ticker);
    if (!existing || f.periodEnd > existing.periodEnd) {
      latest.set(f.ticker, f);
    }
  }

  return latest;
}

/**
 * Get the financial statement from the prior year for growth calculations.
 */
export function getPriorFinancials(asOfDate: string): Map<string, FinancialStatement> {
  const latest = getLatestFinancials(asOfDate);
  const financials = getFinancials();
  const prior = new Map<string, FinancialStatement>();

  for (const [ticker, latestFs] of latest) {
    // Find the annual statement before the latest one
    const candidates = financials
      .filter(f =>
        f.ticker === ticker &&
        f.periodType === "annual" &&
        f.periodEnd < latestFs.periodEnd &&
        f.filingDate <= asOfDate
      )
      .sort((a, b) => b.periodEnd.localeCompare(a.periodEnd));

    if (candidates.length > 0) {
      prior.set(ticker, candidates[0]);
    }
  }

  return prior;
}

/**
 * Get price history for a specific stock (sorted by date ascending).
 */
export function getStockPriceHistory(ticker: string): DailyPrice[] {
  return getPrices()
    .filter(p => p.ticker === ticker)
    .sort((a, b) => a.date.localeCompare(b.date));
}
