import { Link, Navigate, useParams } from 'react-router-dom'
import { useLang, useT } from '../i18n'
import type { Lang, LegalDoc } from '../lib/api'

/**
 * Public offer and personal data policy. The text below is a DRAFT outline until a lawyer provides the final one;
 * when the text changes in substance, bump its version here and in backend/app/domain/legal.py together,
 * so everyone is asked to accept it again.
 */
const VERSION: Record<LegalDoc, string> = { terms: '2026-10-30', privacy: '2026-10-30' }

interface Section {
  title: string
  items: string[]
}
type Doc = Record<Lang, { title: string; sections: Section[] }>

const TERMS: Doc = {
  ru: {
    title: 'Публичная оферта Easo',
    sections: [
      {
        title: '1. Что такое Easo',
        items: [
          'Easo — площадка, на которой поставщики (продавцы) выставляют товары и услуги, а заведения (покупатели) их заказывают.',
          'Easo не является стороной договора поставки: договор заключается между покупателем и продавцом, Easo помогает его оформить.',
        ],
      },
      {
        title: '2. Регистрация',
        items: [
          'Компанию регистрирует человек, который вправе действовать от её имени.',
          'Реквизиты должны совпадать с государственным реестром; для покупок нужна выписка из реестра, для продаж — проверка оператором.',
          'Ставя отметку при регистрации, вы принимаете эту оферту от имени компании.',
        ],
      },
      {
        title: '3. Заказы и торг',
        items: [
          'Цена и условия заказа фиксируются в момент его подтверждения продавцом.',
          'Предложение цены, на которое продавец не ответил в срок, считается отклонённым, а заказ — отменённым.',
          'Счёт, накладная и акт формируются автоматически по данным заказа.',
        ],
      },
      {
        title: '4. Вознаграждение Easo',
        items: [
          'С клиентов, которых продавец привёл сам, комиссия не берётся.',
          'С заказов клиентов, пришедших через Easo, продавец платит комиссию по действующим тарифам.',
        ],
      },
      {
        title: '5. Ответственность',
        items: [
          'За качество, количество и сроки поставки отвечает продавец, за оплату — покупатель.',
          'Easo отвечает за работу площадки и сохранность данных.',
        ],
      },
      {
        title: '6. Изменения оферты',
        items: ['При существенных изменениях Easo попросит принять новую редакцию при следующем входе.'],
      },
    ],
  },
  hy: {
    title: 'Easo հանրային օֆերտա',
    sections: [
      {
        title: '1. Ի՞նչ է Easo-ն',
        items: [
          'Easo-ն հարթակ է, որտեղ մատակարարները (վաճառողները) ներկայացնում են ապրանքներ և ծառայություններ, իսկ հաստատությունները (գնորդները) պատվիրում են դրանք։',
          'Easo-ն մատակարարման պայմանագրի կողմ չէ. պայմանագիրը կնքվում է գնորդի և վաճառողի միջև։',
        ],
      },
      {
        title: '2. Գրանցում',
        items: [
          'Ընկերությունը գրանցում է այն անձը, որն իրավունք ունի գործելու նրա անունից։',
          'Գնումների համար անհրաժեշտ է պետռեգիստրի քաղվածք, վաճառքի համար՝ օպերատորի ստուգում։',
          'Գրանցման ժամանակ նշում կատարելով՝ դուք ընդունում եք այս օֆերտան ընկերության անունից։',
        ],
      },
      {
        title: '3. Պատվերներ և սակարկում',
        items: [
          'Պատվերի գինը և պայմանները ֆիքսվում են վաճառողի հաստատման պահին։',
          'Ժամանակին չպատասխանված գնային առաջարկը համարվում է մերժված, իսկ պատվերը՝ չեղարկված։',
        ],
      },
      {
        title: '4. Easo-ի վարձատրություն',
        items: [
          'Վաճառողի կողմից բերված հաճախորդներից միջնորդավճար չի գանձվում։',
          'Easo-ի միջոցով եկած հաճախորդների պատվերներից վաճառողը վճարում է միջնորդավճար՝ գործող սակագներով։',
        ],
      },
      {
        title: '5. Պատասխանատվություն',
        items: ['Մատակարարման որակի և ժամկետների համար պատասխանատու է վաճառողը, վճարման համար՝ գնորդը։'],
      },
      {
        title: '6. Փոփոխություններ',
        items: ['Էական փոփոխությունների դեպքում Easo-ն կխնդրի ընդունել նոր խմբագրությունը հաջորդ մուտքի ժամանակ։'],
      },
    ],
  },
  en: {
    title: 'Easo public offer',
    sections: [
      {
        title: '1. What Easo is',
        items: [
          'Easo is a marketplace where suppliers (sellers) list goods and services and venues (buyers) order them.',
          'Easo is not a party to the supply contract: it is made between the buyer and the seller, Easo helps to put it on paper.',
        ],
      },
      {
        title: '2. Sign-up',
        items: [
          'A company is registered by a person entitled to act on its behalf.',
          'Buying needs a state register extract; selling needs a check by an operator.',
          'By ticking the box on sign-up you accept this offer on behalf of the company.',
        ],
      },
      {
        title: '3. Orders and offers',
        items: [
          'The price and terms of an order are fixed when the seller confirms it.',
          'A price offer left without an answer in time counts as declined, and the order is cancelled.',
        ],
      },
      {
        title: '4. Easo fees',
        items: [
          'No commission on clients the seller brought themselves.',
          'Orders from clients who came through Easo carry a commission at the current rates.',
        ],
      },
      {
        title: '5. Liability',
        items: ['The seller answers for quality, quantity and delivery time; the buyer for payment.'],
      },
      {
        title: '6. Changes',
        items: ['After a substantial change Easo asks you to accept the new version on your next visit.'],
      },
    ],
  },
}

const PRIVACY: Doc = {
  ru: {
    title: 'Политика обработки персональных данных',
    sections: [
      {
        title: '1. Какие данные мы храним',
        items: [
          'Имя, почта, телефон, аккаунт Telegram или Google — для входа и уведомлений.',
          'Реквизиты компании и загруженные документы — для проверки и выпуска счетов и накладных.',
          'Адреса доставки и данные принимающих — чтобы продавец знал, куда и кому везти.',
        ],
      },
      {
        title: '2. Кто их видит',
        items: [
          'Продавец видит данные покупателя, необходимые для заказа; покупатель — реквизиты продавца.',
          'Документы компании видят только её сотрудники и оператор Easo.',
          'Мы не продаём и не передаём данные третьим лицам, кроме случаев, предусмотренных законом РА.',
        ],
      },
      {
        title: '3. Хранение и удаление',
        items: [
          'Данные хранятся на защищённом сервере; доступ — только по входу.',
          'Документы по заказам хранятся столько, сколько требует налоговое законодательство.',
          'Удалить аккаунт или исправить данные можно по запросу на почту поддержки.',
        ],
      },
    ],
  },
  hy: {
    title: 'Անձնական տվյալների մշակման քաղաքականություն',
    sections: [
      {
        title: '1. Ի՞նչ տվյալներ ենք պահում',
        items: [
          'Անուն, էլ. փոստ, հեռախոս, Telegram կամ Google հաշիվ՝ մուտքի և ծանուցումների համար։',
          'Ընկերության վավերապայմաններ և փաստաթղթեր՝ ստուգման և հաշիվների համար։',
          'Առաքման հասցեներ և ընդունողների տվյալներ։',
        ],
      },
      {
        title: '2. Ո՞վ է դրանք տեսնում',
        items: [
          'Վաճառողը տեսնում է պատվերի համար անհրաժեշտ տվյալները, գնորդը՝ վաճառողի վավերապայմանները։',
          'Մենք չենք վաճառում և չենք փոխանցում տվյալները երրորդ անձանց, բացի ՀՀ օրենքով նախատեսված դեպքերից։',
        ],
      },
      {
        title: '3. Պահպանում և ջնջում',
        items: ['Հաշիվը ջնջելու կամ տվյալները ուղղելու համար գրեք աջակցության էլ. փոստին։'],
      },
    ],
  },
  en: {
    title: 'Personal data policy',
    sections: [
      {
        title: '1. What we keep',
        items: [
          'Name, e-mail, phone, Telegram or Google account — to sign in and send notifications.',
          'Company requisites and uploaded documents — for verification, invoices and delivery notes.',
          'Delivery addresses and receivers — so the seller knows where and to whom to deliver.',
        ],
      },
      {
        title: '2. Who sees it',
        items: [
          'The seller sees the buyer data an order needs; the buyer sees the seller requisites.',
          'We do not sell or pass data to third parties, except where the law of Armenia requires it.',
        ],
      },
      {
        title: '3. Keeping and deleting',
        items: ['To delete the account or correct data, write to the support e-mail.'],
      },
    ],
  },
}

const DOCS: Record<LegalDoc, Doc> = { terms: TERMS, privacy: PRIVACY }

export function Legal() {
  const t = useT()
  const lang = useLang()
  const { doc } = useParams()
  if (doc !== 'terms' && doc !== 'privacy') return <Navigate to="/legal/terms" replace />
  const text = DOCS[doc][lang]
  const [y, m, d] = VERSION[doc].split('-')
  return (
    <div className="legal page">
      <nav className="legal-nav">
        <Link to="/" className="brand">
          Easo
        </Link>
        <Link to="/legal/terms" className={doc === 'terms' ? 'on' : ''}>
          {t('legal_terms')}
        </Link>
        <Link to="/legal/privacy" className={doc === 'privacy' ? 'on' : ''}>
          {t('legal_privacy')}
        </Link>
      </nav>
      <article className="legal-doc">
        <p className="eyebrow">
          {t('legal_version')} {`${d}.${m}.${y}`}
        </p>
        <h1 className="page-title">{text.title}</h1>
        <p className="legal-draft">{t('legal_draft')}</p>
        {text.sections.map((s) => (
          <section key={s.title}>
            <h2>{s.title}</h2>
            <ul>
              {s.items.map((i) => (
                <li key={i}>{i}</li>
              ))}
            </ul>
          </section>
        ))}
      </article>
    </div>
  )
}
