// Gender and skin-tone filters for the featured character on the setup screen.

export const SKIN_TONES: { id: string; color: string }[] = [
  { id: 'light', color: '#F3D5BC' },
  { id: 'medium-light', color: '#DDB08A' },
  { id: 'medium', color: '#BE875C' },
  { id: 'medium-dark', color: '#8E5A38' },
  { id: 'dark', color: '#5A3722' },
];

interface Words {
  gender: string; any: string; female: string; male: string; skin: string;
  tones: [string, string, string, string, string]; none: string;
}

const EN: Words = {
  gender: 'Gender', any: 'Any', female: 'Female', male: 'Male', skin: 'Skin tone',
  tones: ['Light', 'Medium-light', 'Medium', 'Medium-dark', 'Dark'], none: 'No character matches. Try another choice.',
};

// Keyed by base language code; anything missing falls back to English.
const WORDS: Record<string, Words> = {
  en: EN,
  es: { gender: 'Género', any: 'Cualquiera', female: 'Mujer', male: 'Hombre', skin: 'Tono de piel',
    tones: ['Claro', 'Medio claro', 'Medio', 'Medio oscuro', 'Oscuro'], none: 'Ningún personaje coincide. Prueba otra opción.' },
  pt: { gender: 'Género', any: 'Qualquer', female: 'Mulher', male: 'Homem', skin: 'Tom de pele',
    tones: ['Claro', 'Médio claro', 'Médio', 'Médio escuro', 'Escuro'], none: 'Nenhuma personagem corresponde. Experimente outra opção.' },
  af: { gender: 'Geslag', any: 'Enige', female: 'Vroulik', male: 'Manlik', skin: 'Veltoon',
    tones: ['Lig', 'Medium-lig', 'Medium', 'Medium-donker', 'Donker'], none: 'Geen karakter pas nie. Probeer ’n ander keuse.' },
  ar: { gender: 'الجنس', any: 'أي', female: 'أنثى', male: 'ذكر', skin: 'لون البشرة',
    tones: ['فاتح', 'فاتح متوسط', 'متوسط', 'داكن متوسط', 'داكن'], none: 'لا توجد شخصية مطابقة. جرّب خيارًا آخر.' },
  he: { gender: 'מגדר', any: 'הכול', female: 'אישה', male: 'גבר', skin: 'גוון עור',
    tones: ['בהיר', 'בהיר-בינוני', 'בינוני', 'כהה-בינוני', 'כהה'], none: 'אין דמות מתאימה. נסו בחירה אחרת.' },
  eu: { gender: 'Generoa', any: 'Edozein', female: 'Emakumea', male: 'Gizona', skin: 'Azalaren tonua',
    tones: ['Argia', 'Ertain argia', 'Ertaina', 'Ertain iluna', 'Iluna'], none: 'Ez dago bat datorren pertsonaiarik. Probatu beste aukera bat.' },
  ne: { gender: 'लिङ्ग', any: 'कुनै पनि', female: 'महिला', male: 'पुरुष', skin: 'छालाको रङ',
    tones: ['हल्का', 'मध्यम-हल्का', 'मध्यम', 'मध्यम-गाढा', 'गाढा'], none: 'कुनै पात्र मेल खाएन। अर्को विकल्प छान्नुहोस्।' },
  ur: { gender: 'جنس', any: 'کوئی بھی', female: 'خاتون', male: 'مرد', skin: 'جلد کی رنگت',
    tones: ['ہلکی', 'درمیانی ہلکی', 'درمیانی', 'درمیانی گہری', 'گہری'], none: 'کوئی کردار مطابقت نہیں رکھتا۔ کوئی اور انتخاب کریں۔' },
  sw: { gender: 'Jinsia', any: 'Yoyote', female: 'Mwanamke', male: 'Mwanamume', skin: 'Rangi ya ngozi',
    tones: ['Nyeupe', 'Nyeupe kiasi', 'Ya kati', 'Nyeusi kiasi', 'Nyeusi'], none: 'Hakuna mhusika anayelingana. Jaribu chaguo jingine.' },
  zh: { gender: '性别', any: '不限', female: '女', male: '男', skin: '肤色',
    tones: ['浅', '中浅', '中等', '中深', '深'], none: '没有符合的角色，请换一个选项。' },
  'zh-HK': { gender: '性別', any: '不限', female: '女', male: '男', skin: '膚色',
    tones: ['淺', '中淺', '中等', '中深', '深'], none: '冇符合嘅角色，請揀其他選項。' },
};

export const filterWords = (lang: string): Words => WORDS[lang] ?? WORDS[lang.split('-')[0]] ?? EN;
