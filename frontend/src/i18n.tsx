import React, { createContext, useContext, useEffect, useState } from 'react'

type Lang = 'ar' | 'en'

const dict = {
  appName: { ar: 'MedRAG', en: 'MedRAG' },
  tagline: {
    ar: 'منصة ذكاء ومقارنة أسعار الخدمات الطبية',
    en: 'Medical services price intelligence & comparison',
  },
  searchPlaceholder: {
    ar: 'ابحث عن أي فحص أو خدمة… مثال: CBC أو صورة دم كاملة أو MRI Brain',
    en: 'Search any examination… e.g. CBC, صورة دم كاملة, MRI Brain',
  },
  search: { ar: 'بحث', en: 'Search' },
  compare: { ar: 'مقارنة', en: 'Compare' },
  home: { ar: 'الرئيسية', en: 'Home' },
  admin: { ar: 'الإدارة', en: 'Admin' },
  documents: { ar: 'المستندات', en: 'Documents' },
  catalog: { ar: 'الدليل الطبي', en: 'Catalog' },
  reviews: { ar: 'مراجعة', en: 'Reviews' },
  aliases: { ar: 'المسميات', en: 'Aliases' },
  login: { ar: 'دخول', en: 'Login' },
  logout: { ar: 'خروج', en: 'Logout' },
  username: { ar: 'اسم المستخدم', en: 'Username' },
  password: { ar: 'كلمة المرور', en: 'Password' },
  interpretation: { ar: 'تفسير البحث', en: 'Search interpretation' },
  crossRef: { ar: 'المسميات المكافئة', en: 'Cross-reference' },
  priceTable: { ar: 'مقارنة الأسعار', en: 'Price comparison' },
  rank: { ar: '#', en: '#' },
  center: { ar: 'المركز / الفرع', en: 'Medical center' },
  examName: { ar: 'الاسم في المصدر', en: 'Source name' },
  code: { ar: 'الكود', en: 'Code' },
  price: { ar: 'السعر', en: 'Price' },
  category: { ar: 'فئة السعر', en: 'Price category' },
  year: { ar: 'سنة التعرفة', en: 'Tariff year' },
  currency: { ar: 'العملة', en: 'Currency' },
  city: { ar: 'المدينة', en: 'City' },
  effective: { ar: 'سارى من', en: 'Effective' },
  source: { ar: 'المصدر', en: 'Source' },
  page: { ar: 'صفحة', en: 'Page' },
  conditions: { ar: 'شروط', en: 'Conditions' },
  confidence: { ar: 'الثقة', en: 'Confidence' },
  providerUnspecified: { ar: 'المركز غير محدد في المصدر', en: 'Provider not specified in source' },
  noResults: { ar: 'لا توجد نتائج مطابقة', en: 'No matching results' },
  suggestions: { ar: 'اقتراحات', en: 'Suggestions' },
  filters: { ar: 'تصفية', en: 'Filters' },
  allCategories: { ar: 'كل التخصصات', en: 'All categories' },
  allCities: { ar: 'كل المدن', en: 'All cities' },
  allProviders: { ar: 'كل المراكز', en: 'All providers' },
  allPriceCats: { ar: 'كل فئات السعر', en: 'All price categories' },
  allYears: { ar: 'كل السنوات', en: 'All years' },
  cheapest: { ar: 'الأقل سعرًا', en: 'Cheapest' },
  records: { ar: 'سجل', en: 'records' },
  upload: { ar: 'رفع مستند', en: 'Upload document' },
  ingestFolder: { ar: 'فهرسة مجلد', en: 'Ingest folder' },
  stats: { ar: 'إحصاءات', en: 'Statistics' },
  approved: { ar: 'معتمد', en: 'Approved' },
  proposed: { ar: 'مقترح', en: 'Proposed' },
  rejected: { ar: 'مرفوض', en: 'Rejected' },
  approve: { ar: 'اعتماد', en: 'Approve' },
  reject: { ar: 'رفض', en: 'Reject' },
  save: { ar: 'حفظ', en: 'Save' },
  cancel: { ar: 'إلغاء', en: 'Cancel' },
  addAlias: { ar: 'إضافة مسمى', en: 'Add alias' },
  merge: { ar: 'دمج', en: 'Merge' },
  correctPrice: { ar: 'تصحيح سعر', en: 'Correct price' },
  reason: { ar: 'السبب', en: 'Reason' },
  loading: { ar: 'جار التحميل…', en: 'Loading…' },
  tryExamples: { ar: 'أمثلة', en: 'Try' },
  needLogin: { ar: 'هذه الصفحة تتطلب تسجيل الدخول', en: 'This page requires login' },
  documentsIngested: { ar: 'مستندات مفهرسة', en: 'Documents ingested' },
  canonicalExams: { ar: 'فحوص قياسية', en: 'Canonical examinations' },
  priceRecords: { ar: 'سجلات أسعار', en: 'Price records' },
  providers: { ar: 'مراكز خدمة', en: 'Providers' },
  pendingReviews: { ar: 'بانتظار المراجعة', en: 'Pending reviews' },
  checksum: { ar: 'البصمة', en: 'Checksum' },
  extraction: { ar: 'حالة الاستخراج', en: 'Extraction' },
  associateProvider: { ar: 'ربط بمركز', en: 'Associate provider' },
  supersede: { ar: 'إيقاف التعرفة', en: 'Supersede tariff' },
  backToSearch: { ar: 'عودة للبحث', en: 'Back to search' },
}

interface I18nCtx {
  lang: Lang
  setLang: (l: Lang) => void
  t: (k: keyof typeof dict) => string
  dir: 'rtl' | 'ltr'
}

const Ctx = createContext<I18nCtx>(null as any)

export function I18nProvider({ children }: { children: React.ReactNode }) {
  const [lang, setLang] = useState<Lang>(() => (localStorage.getItem('medrag.lang') as Lang) || 'ar')
  useEffect(() => {
    localStorage.setItem('medrag.lang', lang)
    document.documentElement.lang = lang
    document.documentElement.dir = lang === 'ar' ? 'rtl' : 'ltr'
  }, [lang])
  const t = (k: keyof typeof dict) => dict[k]?.[lang] ?? String(k)
  return (
    <Ctx.Provider value={{ lang, setLang, t, dir: lang === 'ar' ? 'rtl' : 'ltr' }}>
      {children}
    </Ctx.Provider>
  )
}

export const useI18n = () => useContext(Ctx)
