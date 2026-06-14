// =============================================================================
// Internationalisation — English labels for Korean data
// =============================================================================
// Two helpers:
//
//   displayName(stock)  ->  English name when available, Korean fallback
//   translateIndustry(industry_kr)  ->  English label for a KSIC industry
//
// The industry translations cover the ~150 distinct KSIC categories that
// appear in our `stocks.industry` column. Anything not in the map falls back
// to the original Korean text — so adding coverage just means appending
// entries to INDUSTRY_KR_TO_EN below.
// =============================================================================

import type { Stock } from "@/types";

/**
 * Display name: English when we have it, Korean otherwise.
 * (Some 100% of stocks have `nameEn` after the DART backfill, but keep the
 * fallback for safety.)
 */
export function displayName(stock: Pick<Stock, "name" | "nameEn">): string {
  return (stock.nameEn && stock.nameEn.trim()) || stock.name;
}

/**
 * Translate a Korean KSIC industry label to English. Returns the original
 * string if no translation is registered — that's deliberate so untranslated
 * industries are still visible (in Korean) instead of falling back to
 * "Unknown" or similar.
 */
export function translateIndustry(industry: string | null | undefined): string | null {
  if (!industry) return null;
  const trimmed = industry.trim();
  return INDUSTRY_KR_TO_EN[trimmed] ?? trimmed;
}

/**
 * Korean KSIC industry label → English. Curated from the Statistics Korea
 * KSIC translation reference. Extend by appending entries — order doesn't
 * matter, it's a dict lookup.
 */
const INDUSTRY_KR_TO_EN: Record<string, string> = {
  // ---------- Manufacturing — chemicals, materials, pharma ----------
  "기초 화학물질 제조업": "Basic Chemicals Manufacturing",
  "기타 화학제품 제조업": "Other Chemicals Manufacturing",
  "합성고무 및 플라스틱 물질 제조업": "Synthetic Rubber and Plastics Manufacturing",
  "비료, 농약 및 살균·살충제 제조업": "Fertilizers, Pesticides and Agrochemicals Manufacturing",
  "플라스틱 제품 제조업": "Plastic Products Manufacturing",
  "고무제품 제조업": "Rubber Products Manufacturing",
  "유리 및 유리제품 제조업": "Glass and Glass Products Manufacturing",
  "도자기 및 기타 요업제품 제조업": "Ceramics and Other Pottery Manufacturing",
  "시멘트, 석회, 플라스터 및 그 제품 제조업": "Cement, Lime, Plaster Manufacturing",
  "기타 비금속 광물제품 제조업": "Other Non-metallic Mineral Products Manufacturing",
  "기초 의약물질 및 생물학적 제제 제조업": "Pharmaceutical Substances and Biologics Manufacturing",
  "의약품 제조업": "Pharmaceutical Manufacturing",
  "의료용품 및 기타 의약 관련제품 제조업": "Medical Supplies and Pharmaceutical Products Manufacturing",
  "의료용 기기 제조업": "Medical Instruments Manufacturing",
  "화장품 제조업": "Cosmetics Manufacturing",
  "비누 및 세제, 화장품 및 광택제 제조업": "Soap, Detergents and Polishes Manufacturing",

  // ---------- Manufacturing — metals ----------
  "1차 철강 제조업": "Primary Iron and Steel Manufacturing",
  "1차 비철금속 제조업": "Primary Non-ferrous Metal Manufacturing",
  "금속주조업": "Metal Casting",
  "구조용 금속제품, 탱크 및 증기발생기 제조업": "Structural Metals and Tanks Manufacturing",
  "기타 금속 가공제품 제조업": "Other Fabricated Metal Products Manufacturing",
  "금속 가공제품 제조업; 기계 및 가구 제외": "Metal Fabrication (excl. Machinery and Furniture)",

  // ---------- Manufacturing — electronics / semiconductors ----------
  "반도체 제조업": "Semiconductor Manufacturing",
  "전자부품 제조업": "Electronic Components Manufacturing",
  "통신 및 방송 장비 제조업": "Telecom and Broadcasting Equipment Manufacturing",
  "영상 및 음향기기 제조업": "Audio-Visual Equipment Manufacturing",
  "컴퓨터 및 주변장치 제조업": "Computers and Peripherals Manufacturing",
  "전기장비 제조업": "Electrical Equipment Manufacturing",
  "절연선 및 케이블 제조업": "Insulated Wire and Cable Manufacturing",
  "축전지 및 일차전지 제조업": "Batteries and Storage Cells Manufacturing",
  "전구 및 조명장치 제조업": "Lighting Equipment Manufacturing",
  "가정용 전기기기 제조업": "Household Electrical Appliances Manufacturing",

  // ---------- Manufacturing — machinery / vehicles ----------
  "기타 기계 및 장비 제조업": "Other Machinery and Equipment Manufacturing",
  "일반 목적용 기계 제조업": "General-Purpose Machinery Manufacturing",
  "기타 특수목적용 기계 제조업": "Other Special-Purpose Machinery Manufacturing",
  "농업 및 임업용 기계 제조업": "Agricultural and Forestry Machinery Manufacturing",
  "자동차용 엔진 및 자동차 제조업": "Automobiles and Engines Manufacturing",
  "자동차 신품 부품 제조업": "Automotive Parts Manufacturing",
  "자동차 차체 및 트레일러 제조업": "Auto Bodies and Trailers Manufacturing",
  "선박 및 보트 건조업": "Shipbuilding and Boatbuilding",
  "철도장비 제조업": "Railway Equipment Manufacturing",
  "항공기, 우주선 및 부품 제조업": "Aerospace and Parts Manufacturing",
  "기타 운송장비 제조업": "Other Transport Equipment Manufacturing",

  // ---------- Manufacturing — food / textiles / wood / paper ----------
  "도축, 육류 가공 및 저장 처리업": "Meat Processing and Preservation",
  "수산물 가공 및 저장 처리업": "Fish Processing and Preservation",
  "과실, 채소 가공 및 저장 처리업": "Fruit and Vegetable Processing",
  "동·식물성 유지 제조업": "Animal and Vegetable Oils Manufacturing",
  "낙농제품 및 식용빙과류 제조업": "Dairy and Frozen Desserts Manufacturing",
  "곡물가공품, 전분 및 전분제품 제조업": "Grain Mill and Starch Products Manufacturing",
  "기타 식품 제조업": "Other Food Manufacturing",
  "음·식료품 제조업": "Food and Beverage Manufacturing",
  "알코올음료 제조업": "Alcoholic Beverages Manufacturing",
  "비알코올음료 및 얼음 제조업": "Soft Drinks and Ice Manufacturing",
  "담배제조업": "Tobacco Manufacturing",
  "방적 및 가공사 제조업": "Spinning and Yarn Manufacturing",
  "직물직조 및 직물제품 제조업": "Woven Textiles Manufacturing",
  "편조원단 및 의류 제조업": "Knit Fabrics and Apparel Manufacturing",
  "기타 섬유제품 제조업": "Other Textile Products Manufacturing",
  "의복 액세서리 제조업": "Apparel Accessories Manufacturing",
  "신발 및 신발 부분품 제조업": "Footwear and Components Manufacturing",
  "가죽, 가방 및 유사제품 제조업": "Leather and Luggage Manufacturing",
  "목재 및 나무제품 제조업; 가구 제외": "Wood Products Manufacturing (excl. Furniture)",
  "펄프, 종이 및 판지 제조업": "Pulp, Paper, and Paperboard Manufacturing",
  "종이 상자 및 종이 용기 제조업": "Paper Containers Manufacturing",
  "기타 종이 및 판지 제품 제조업": "Other Paper Products Manufacturing",
  "인쇄 및 기록매체 복제업": "Printing and Reproduction Services",
  "가구 제조업": "Furniture Manufacturing",
  "기타 제품 제조업": "Other Manufacturing",
  "산업용 가스 제조업": "Industrial Gas Manufacturing",
  "코크스, 연탄 및 기타 연료 제품 제조업": "Coke and Solid Fuels Manufacturing",
  "정유업": "Petroleum Refining",

  // ---------- Mining ----------
  "석탄, 원유 및 천연가스 광업": "Mining of Coal, Crude Petroleum and Natural Gas",
  "금속광업": "Metal Mining",
  "비금속광물 광업; 연료용 제외": "Non-metallic Mineral Mining (excl. Fuels)",

  // ---------- Utilities ----------
  "전기업": "Electric Power Generation, Transmission and Distribution",
  "가스 제조 및 배관공급업": "Gas Manufacture and Distribution via Pipeline",
  "증기, 냉·온수 및 공기조절 공급업": "Steam, Hot/Cold Water and Air Conditioning Supply",
  "수도사업": "Water Supply",
  "환경 정화 및 복원업": "Environmental Remediation Services",
  "폐기물 수집, 운반, 처리 및 원료재생업": "Waste Management and Recycling",

  // ---------- Construction ----------
  "종합 건설업": "General Construction",
  "토목 건설업": "Civil Engineering Construction",
  "건물 건설업": "Building Construction",
  "전문직별 공사업": "Specialty Construction",
  "기반조성 및 시설물 축조관련 전문공사업": "Site Preparation and Specialty Construction",

  // ---------- Wholesale and retail ----------
  "상품 종합 도매업": "General Merchandise Wholesale",
  "음·식료품 도매업": "Food and Beverage Wholesale",
  "기계장비 및 관련 물품 도매업": "Machinery and Equipment Wholesale",
  "건축자재, 철물 및 난방장치 도매업": "Building Materials, Hardware and Heating Wholesale",
  "기타 전문 도매업": "Other Specialized Wholesale",
  "체인화 편의점": "Convenience Stores",
  "기타 상품 전문 소매업": "Other Specialized Retail",
  "음·식료품 및 담배 소매업": "Food, Beverage and Tobacco Retail",
  "섬유, 의복, 신발 및 가죽제품 소매업": "Apparel and Footwear Retail",
  "기타 가정용품 소매업": "Other Household Goods Retail",
  "문화, 오락 및 여가 용품 소매업": "Cultural, Recreation and Leisure Goods Retail",
  "통신 판매업": "E-commerce / Telemarketing Retail",
  "백화점": "Department Stores",
  "대형 종합 소매업": "Large Department Stores",
  "무점포 소매업": "Non-store Retail",

  // ---------- Transport and logistics ----------
  "여객철도운송업": "Passenger Rail Transport",
  "철도화물운송업": "Freight Rail Transport",
  "도로 여객 운송업": "Road Passenger Transport",
  "도로 화물 운송업": "Road Freight Transport",
  "외항 여객 운송업": "Ocean Passenger Transport",
  "외항 화물 운송업": "Ocean Freight Transport",
  "항공 여객 운송업": "Air Passenger Transport",
  "항공 화물 운송업": "Air Freight Transport",
  "보관 및 창고업": "Warehousing and Storage",
  "기타 운송 관련 서비스업": "Other Transport-Related Services",
  "물류 보조 서비스업": "Logistics Support Services",
  "우편 및 통신업": "Postal and Telecommunications",

  // ---------- Information and communications ----------
  "자료처리, 호스팅, 포털 및 기타 인터넷 정보매개 서비스업":
    "Data Processing, Hosting, Portals and Internet Intermediation Services",
  "포털 및 기타 인터넷 정보매개 서비스업": "Portals and Other Internet Intermediation Services",
  "소프트웨어 개발 및 공급업": "Software Development and Publishing",
  "컴퓨터 프로그래밍, 시스템 통합 및 관리업":
    "Computer Programming, Systems Integration and Management",
  "정보 서비스업": "Information Services",
  "유선 통신업": "Wired Telecommunications",
  "무선 및 위성 통신업": "Wireless and Satellite Telecommunications",
  "방송업": "Broadcasting",
  "영상 및 음향기기 출판업": "Audio-Visual Publishing",
  "영화, 비디오물, 방송프로그램 제작 및 배급업":
    "Motion Picture, Video and TV Programme Production / Distribution",
  "음악 및 기타 오디오 출판업": "Music and Audio Publishing",

  // ---------- Finance ----------
  "은행 및 저축기관": "Banks and Savings Institutions",
  "투자기관": "Investment Institutions",
  "기타 금융업": "Other Financial Services",
  "신용카드 및 할부금융업": "Credit Card and Consumer Finance",
  "보험업": "Insurance",
  "재보험업": "Reinsurance",
  "보험 및 연금 관련 서비스업": "Insurance and Pension Auxiliary Services",
  "금융지원 서비스업": "Financial Auxiliary Services",
  "부동산 임대업": "Real Estate Leasing",
  "부동산 매매업": "Real Estate Sales",
  "부동산 관련 서비스업": "Real Estate Services",

  // ---------- Professional, scientific, and technical ----------
  "법무 관련 서비스업": "Legal Services",
  "회계 및 세무 관련 서비스업": "Accounting and Tax Services",
  "광고업": "Advertising",
  "시장조사 및 여론조사업": "Market Research and Polling",
  "전문 디자인업": "Specialised Design Services",
  "사진촬영 및 처리업": "Photography Services",
  "기타 전문 과학 및 기술 서비스업": "Other Professional, Scientific and Technical Services",
  "건축 기술, 엔지니어링 및 관련 기술 서비스업":
    "Architectural, Engineering and Related Technical Services",
  "자연과학 및 공학 연구개발업": "Natural Sciences and Engineering R&D",
  "인문 및 사회과학 연구개발업": "Humanities and Social Sciences R&D",

  // ---------- Business support ----------
  "사업지원 서비스업": "Business Support Services",
  "회사 본부 및 경영컨설팅 서비스업": "Head Offices and Management Consulting",
  "고용 알선 및 인력 공급업": "Employment Placement and Staffing",
  "여행사 및 기타 여행보조 서비스업": "Travel Agencies and Tour Operators",
  "보안 시스템 서비스업": "Security Systems Services",
  "사업시설 유지·관리 및 조경 서비스업": "Building Maintenance and Landscaping",

  // ---------- Services ----------
  "교육 서비스업": "Education Services",
  "고등 교육기관": "Higher Education Institutions",
  "교양 및 직업 훈련학원": "Cultural and Vocational Training",
  "사회복지 서비스업": "Social Welfare Services",
  "보건업": "Healthcare Services",
  "예술, 스포츠 및 여가관련 서비스업": "Arts, Sports and Recreation Services",
  "창작 및 예술관련 서비스업": "Creative and Arts Services",
  "스포츠 서비스업": "Sports Services",
  "유원지 및 기타 오락관련 서비스업": "Amusement and Other Recreation Services",
  "도서관, 사적지 및 유사 여가관련 서비스업": "Libraries, Heritage and Similar Services",
  "수리업": "Repair and Maintenance Services",
  "기타 개인 서비스업": "Other Personal Services",
  "협회 및 단체": "Membership Organisations",

  // ---------- Hospitality and food service ----------
  "숙박업": "Accommodation",
  "음식점 및 주점업": "Restaurants and Bars",
  "종합소득세 신고 및 부동산임대업":
    "Consolidated Income Tax Filing and Real Estate Leasing",

  // ---------- Agriculture / Fisheries / Forestry ----------
  "작물 재배업": "Crop Production",
  "축산업": "Livestock Farming",
  "임업": "Forestry",
  "어업": "Fisheries",
  "어로 어업": "Marine Fisheries",

  // ---------- Additional KSIC categories observed in 2026-06 data ----------
  // Variants (different spacing/punctuation) of entries above are listed
  // explicitly so the exact KRX strings hit a translation.
  "상품 중개업": "Commodity Brokerage",
  "음·식료품 및 담배 도매업": "Food, Beverage and Tobacco Wholesale",
  "산업용 농·축산물 및 동·식물 도매업": "Industrial Agriculture and Livestock Wholesale",
  "운송장비 도매업": "Transport Equipment Wholesale",
  "자동차 판매업": "Auto Dealers",
  "자동차 부품 및 내장품 판매업": "Auto Parts and Accessories Retail",
  "가전제품 및 정보통신장비 소매업": "Consumer Electronics and IT Retail",
  "전자제품, 컴퓨터, 통신장비 및 부품 도매업": "Electronics, Computers and Telecom Equipment Wholesale",
  "종합 소매업": "General Retail",
  "그외 기타 제품 제조업": "Other Miscellaneous Products Manufacturing",
  "그외 기타 운송장비 제조업": "Other Miscellaneous Transport Equipment Manufacturing",
  "특수 목적용 기계 제조업": "Special-Purpose Machinery Manufacturing",
  "내화, 비내화 요업제품 제조업": "Refractory and Non-Refractory Ceramics Manufacturing",
  "기초 의약물질 제조업": "Basic Pharmaceutical Substances Manufacturing",
  "동·식물성 유지 및 낙농제품 제조업": "Animal/Vegetable Oils and Dairy Products Manufacturing",
  "악기 제조업": "Musical Instruments Manufacturing",
  "전동기, 발전기 및 전기 변환 · 공급 · 제어 장치 제조업":
    "Motors, Generators and Power Conversion/Control Equipment Manufacturing",
  "기록매체 복제업": "Record Media Reproduction",
  "측정, 시험, 항해, 제어 및 기타 정밀기기 제조업; 광학기기 제외":
    "Measuring, Testing, Navigation and Precision Instruments Manufacturing (excl. Optical)",
  "사진장비 및 광학기기 제조업": "Photographic and Optical Equipment Manufacturing",
  "동물용 사료 및 조제식품 제조업": "Animal Feed and Prepared Feed Manufacturing",
  "기타 전기장비 제조업": "Other Electrical Equipment Manufacturing",
  "비료, 농약 및 살균, 살충제 제조업":
    "Fertilizers, Pesticides and Disinfectants Manufacturing",
  "골판지, 종이 상자 및 종이용기 제조업": "Corrugated and Paper Containers Manufacturing",
  "화학섬유 제조업": "Synthetic Fibers Manufacturing",
  "플라스틱제품 제조업": "Plastic Products Manufacturing",
  "가정용 기기 제조업": "Household Appliances Manufacturing",
  "봉제의복 제조업": "Sewn Apparel Manufacturing",
  "석유 정제품 제조업": "Petroleum Products Manufacturing",
  "담배 제조업": "Tobacco Manufacturing",
  "건물설비 설치 공사업": "Building Equipment Installation",
  "전기 및 통신 공사업": "Electrical and Telecom Construction",
  "실내건축 및 건축마무리 공사업": "Interior Construction and Finishing",
  "건축기술, 엔지니어링 및 관련 기술 서비스업":
    "Architectural, Engineering and Related Technical Services",
  "회사 본부 및 경영 컨설팅 서비스업": "Head Offices and Management Consulting",
  "일반 교습 학원": "General Tutoring Academies",
  "기타 교육기관": "Other Educational Institutions",
  "그외 기타 전문, 과학 및 기술 서비스업":
    "Other Professional, Scientific and Technical Services",
  "기타 사업지원 서비스업": "Other Business Support Services",
  "그외 기타 개인 서비스업": "Other Personal Services",
  "경비, 경호 및 탐정업": "Security and Detective Services",
  "기타 정보 서비스업": "Other Information Services",
  "전기 통신업": "Telecommunications",
  "텔레비전 방송업": "Television Broadcasting",
  "영상·오디오물 제공 서비스업": "Video and Audio Content Services",
  "오디오물 출판 및 원판 녹음업": "Audio Publishing and Recording",
  "영상·오디오물 출판업": "Video and Audio Publishing",
  "서적, 잡지 및 기타 인쇄물 출판업": "Books, Magazines and Other Print Publishing",
  "보험 및 연금관련 서비스업": "Insurance and Pension Auxiliary Services",
  "금융 지원 서비스업": "Financial Auxiliary Services",
  "재 보험업": "Reinsurance",
  "부동산 임대 및 공급업": "Real Estate Leasing and Supply",
  "비주거용 건물 임대업": "Non-residential Real Estate Leasing",
  "주거용 건물 임대업": "Residential Real Estate Leasing",
  "기타 운송관련 서비스업": "Other Transport-Related Services",
  "해상 운송업": "Maritime Transport",
  "항공기,우주선 및 부품 제조업": "Aerospace and Parts Manufacturing",
  "연료용 가스 제조 및 배관공급업": "Fuel Gas Manufacture and Pipeline Distribution",
  "개인 및 가정용품 임대업": "Personal and Household Goods Rental",
  "폐기물 처리업": "Waste Treatment",
  "비내화 요업제품 제조업": "Non-Refractory Ceramics Manufacturing",
  "일차전지 및 이차전지 제조업": "Primary and Secondary Batteries Manufacturing",
  "기타 전문 서비스업": "Other Specialised Services",
  "생활용품 도매업": "Household Goods Wholesale",
  "자동차 차체나 트레일러 제조업": "Auto Bodies and Trailers Manufacturing",
};

export const INDUSTRY_TRANSLATION_COUNT = Object.keys(INDUSTRY_KR_TO_EN).length;
