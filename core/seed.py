"""Default categories, merchant rules, members and cash wallets. Safe to run many times."""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from core.config import Settings, settings
from core.db import Account, Category, Kind, Member, MerchantRule, Role

LIVING = "Living"
FAMILY = "Family support"
SAVINGS = "Savings and investments"
LOANS = "Loans and EMIs"
GROUPS = [LIVING, FAMILY, SAVINGS, LOANS]

# Same names as the 2026 sheet, so history and new data line up.
CATEGORIES: list[tuple[str, str]] = [
    ("Food & Groceries", LIVING),
    ("Travel / Fuel", LIVING),
    ("Monthly Bills", LIVING),
    ("Shopping", LIVING),
    ("Subscription", LIVING),
    ("Medical", LIVING),
    ("Learning", LIVING),
    ("Entertainment", LIVING),
    ("Vacation Plan", LIVING),
    ("Miscellaneous", LIVING),
    ("Cash, not itemized", LIVING),
    ("Family support", FAMILY),
    ("Investments", SAVINGS),
    ("Debt / Loans", LOANS),
]

# keyword -> (category, kind, always_ask). Keywords are matched inside the payee name
# and the part of the UPI ID before "@" (so "uber@paytm" matches "uber", not "paytm").
MERCHANT_RULES: dict[str, tuple[str, Kind, bool]] = {}


def _add(category: str, words: str, kind: Kind = Kind.spent, always_ask: bool = False) -> None:
    for w in words.split(","):
        w = w.strip().lower()
        if w:
            MERCHANT_RULES[w] = (category, kind, always_ask)


_add("Food & Groceries", "swiggy,zomato,zepto,blinkit,grofers,bigbasket,instamart,dunzo,jiomart,dmart,avenue supermarts,"
     "ratnadeep,licious,freshtohome,country delight,milkbasket,dominos,mcdonald,kfc,starbucks,eatsure,box8,haldiram")
_add("Travel / Fuel", "uber,olacabs,ola cabs,rapido,irctc,redbus,makemytrip,goibibo,indigo,interglobe,air india,akasa,"
     "cleartrip,ixigo,hpcl,iocl,indian oil,indianoil,bpcl,bharat petroleum,shell,fastag,namma yatri,metro rail,abhibus")
_add("Monthly Bills", "airtel,jio,reliance jio,vodafone,vi postpaid,bsnl,act fibernet,actcorp,tata play,tataplay,"
     "electricity,tsspdcl,tgspdcl,apspdcl,apepdcl,bescom,msedcl,tneb,tangedco,indane,hp gas,hpgas,bharatgas,"
     "water board,hmwssb,mygate,broadband,piped gas,bhagyanagar gas")
_add("Shopping", "flipkart,myntra,ajio,meesho,nykaa,croma,reliance digital,ikea,decathlon,lifestyle,westside,trends,"
     "tata cliq,pantaloons,lenskart")
_add("Shopping", "amazon,amzn", always_ask=True)  # Amazon sells everything: suggest Shopping, but ask
_add("Subscription", "netflix,spotify,hotstar,jiocinema,primevideo,prime video,youtube,google play,apple.com,icloud,"
     "linkedin,openai,chatgpt,anthropic,claude.ai,zee5,sonyliv")
_add("Medical", "apollo,pharmeasy,1mg,netmeds,medplus,practo,hospital,clinic,diagnostic,pharmacy")
_add("Learning", "udemy,coursera,byju,unacademy,upgrad,simplilearn")
_add("Entertainment", "bookmyshow,pvr,inox,cinepolis")
_add("Investments", "zerodha,groww,upstox,kuvera,indmoney,paytm money,mutual fund,nps trust,safegold,digigold,mmtc,"
     "icici pru,hdfc mf,sbi mf,lic of india")
_add("Debt / Loans", "bajaj finance,bajaj finserv,home credit,loan emi")
# Paying a credit card bill moves money between your own accounts: not spending.
_add("Miscellaneous", "cred.club,cred club,credit card bill,cc payment,cardbill", kind=Kind.self_transfer)
# Wallets and payment gateways hide the real merchant: always ask.
_add("Miscellaneous", "paytm wallet,phonepe wallet,razorpay,payu,cashfree,billdesk", always_ask=True)


def seed(s: Session, cfg: Settings | None = None) -> None:
    cfg = cfg or settings()
    cats = {c.name: c for c in s.scalars(select(Category))}
    for name, group in CATEGORIES:
        if name not in cats:
            c = Category(name=name, group=group)
            s.add(c)
            cats[name] = c
    s.flush()

    existing_rules = {r.keyword for r in s.scalars(select(MerchantRule))}
    for kw, (cat, kind, ask) in MERCHANT_RULES.items():
        if kw not in existing_rules:
            s.add(MerchantRule(keyword=kw, category_id=cats[cat].id, kind=kind, always_ask=ask))

    for name, email, role in (
        (cfg.admin_name, cfg.admin_email or "admin@example.com", Role.admin),
        (cfg.member_name, cfg.member_email or "member@example.com", Role.member),
    ):
        m = s.scalar(select(Member).where(Member.name == name))
        if not m:
            m = Member(name=name, google_email=email, role=role)
            s.add(m)
            s.flush()
        elif cfg.admin_email or cfg.member_email:
            m.google_email = email
            m.role = role
        if not s.scalar(select(Account).where(Account.member_id == m.id, Account.kind == "cash")):
            s.add(Account(member_id=m.id, name=f"Cash wallet ({name})", kind="cash", currency="INR"))
    s.flush()
