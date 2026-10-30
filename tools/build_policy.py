"""Собирает зашифрованные правила policy.enc и подпись safety.sig.

Запускать из папки программы:  venv\\Scripts\\python tools\\build_policy.py
После любого изменения safety.py или правил подпись надо пересобрать этим скриптом.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import safety  # noqa: E402

B = r"\b"

# ── откровенный сексуальный контент (porn / nsfw) ──
PORN_WORD = [
    B + r"(nude|nudes|nudity|naked|nsfw|porn\w*|hentai|xxx|erotic\w*|orgasm\w*|masturbat\w*|blowjob\w*|handjob\w*|"
        r"cumshot\w*|creampie|gangbang\w*|genital\w*|vagina\w*|penis\w*|pussy|dildo\w*|boobs?|tits|titties|nipples?|"
        r"topless|bottomless|undress\w*|unclothed|stripper\w*|striptease|fetish\w*|bdsm|bondage|lewd|onlyfans|camgirl|"
        r"cunt|anal sex|oral sex|having sex|sex (scene|act|tape|toy|position)s?|sexual (act|intercourse)|"
        r"explicit (content|sexual|image|scene))" + B,
    B + r"(no|without|zero|take off|remove) (any |her |his |all |the )?(clothes|clothing|cloth|dress|bra|panties|underwear)" + B,
    B + r"(bare|exposed|naked) (breasts?|chest|body|butt|ass)" + B,
    B + r"p ?o ?r ?n" + B, B + r"n ?s ?f ?w" + B, B + r"n ?u ?d ?e" + B, B + r"n ?a ?k ?e ?d" + B,
    B + r"(порн\w*|хентай\w*|эротик\w*|эротическ\w*|оргазм\w*|мастурб\w*|минет\w*|сперм\w*|гениталии|вагин\w*|пенис\w*|"
        r"хуй\w*|хуя\w*|пизд\w*|ебат\w*|ебл\w*|трахат\w*|трахн\w*|дилдо|сиськ\w*|сисек|соски|гол(?:ая|ую|ый|ые|ых|ой|ого|ому|ым|ыми|еньк\w*)|"
        r"голышом|наг(?:ая|ую|ой|ие|их|ого)|обнажён\w*|обнажен\w*|топлесс|раздет\w*|раздев\w*|стриптиз\w*|фетиш\w*|бдсм|секс|секса|сексом|"
        r"сексу|без одежды|без трусов|без лифчика|снять одежду)" + B,
    B + r"анал(ьн\w*)?" + B,
]
PORN_COMPACT = ["pornograph", "hentai", "blowjob", "handjob", "cumshot", "gangbang", "creampie", "genitals",
                "хентай", "мастурбац", "гениталии"]

# ── gore ──
GORE_WORD = [
    B + r"(gore(?! ?tex)|gory|mutilat\w*|dismember\w*|decapitat\w*|behead\w*|entrails|intestines|disembowel\w*|eviscerat\w*|flayed|"
        r"skinned alive|severed (head|heads|limb|limbs|arm|arms|leg|legs|hand|hands)|blood ?bath|snuff|torture porn|"
        r"torn apart|ripped apart|guts spilling|brain matter|exposed brain|gouged eyes?|pile of corpses|mass grave)" + B,
    B + r"(расчленен\w*|расчленёнк\w*|расчлен\w*|обезглавл\w*|отрубленн\w* (голов\w*|рук\w*|ног\w*|кисть)|выпотрош\w*|"
        r"кишк\w* (наружу|вываливаются)|кровавая баня|снафф|изувечен\w*|вспоротый живот|вспоротым животом|"
        r"выколот\w* глаз\w*|груда трупов|куча трупов)" + B,
]
GORE_COMPACT = ["dismember", "decapitat", "mutilat", "расчленен", "обезглавл"]

# ── несовершеннолетние + сексуальное (hard-блок, не отключается) ──
MINOR_WORD = [
    B + r"(child|children|kid|kids|minor|minors|underage|under ?age|preteen|pre-teen|toddler|baby|infant|schoolgirl\w*|"
        r"schoolboy\w*|school girl|school boy|teen|teens|teenage\w*|young girl|young boy|little girl|little boy|juvenile)" + B,
    B + r"([5-9]|1[0-7]) ?(yo|y\.?o\.?|y/o|years? ?old|yrs?)" + B,
    B + r"(ребён\w*|ребен\w*|дети|детей|детям|детях|детск\w*|малолетк\w*|школьниц\w*|школьник\w*|подрост\w*|"
        r"несовершеннолетн\w*|девочк\w*|мальчик\w*|малыш\w*|младенц\w*)" + B,
    B + r"([5-9]|1[0-7]) ?(лет|летн\w*|годик\w*)" + B,
]
CSAM_DIRECT_WORD = [
    B + r"(loli|lolicon|lolita|shota|shotacon|pedo\w*|pedophil\w*|child ?porn\w*|kiddie ?porn\w*)" + B,
    B + r"(лоли\w*|шота\w*|педофил\w*|педо|детск\w* порн\w*)" + B,
]
SEXUAL_WORD = [
    B + r"(sexy|seductive|sexual\w*|sex|erotic\w*|lewd|ecchi|nude|naked|topless|undress\w*|fetish\w*|panties|lingerie|"
        r"suggestive|provocative|intimate|bikini model)" + B,
    B + r"(сексуальн\w*|секс\w*|соблазнительн\w*|эротич\w*|эротик\w*|гол(?:ая|ую|ый|ые|ых|ой|ого|ому|ым|ыми)|обнаж\w*|раздет\w*|интимн\w*|трусики|"
        r"нижнее бельё|нижнее белье|пошл\w*|провокационн\w*)" + B,
]

# ── попытки обхода (jailbreak / prompt injection) — для усиления защиты в чате ──
JAILBREAK_WORD = [
    B + r"(ignore|disregard|forget|override|bypass|disable|turn off|remove) (all |any |your |the |previous |prior |above |safety |content )*"
        r"(instructions?|rules?|guidelines?|restrictions?|filters?|policy|policies|limits?|safeguards?)" + B,
    B + r"(developer mode|dan mode|jailbreak\w*|do anything now|uncensored|no restrictions|without restrictions|unfiltered|"
        r"no (filter|censorship)|god mode)" + B,
    B + r"(игнорируй|забудь|отключи|обойди|сними|убери|отмени|выключи) (все |любые |свои |эти |прежние |предыдущие |"
        r"твои |безопасн\w* )*(инструкци\w*|правил\w*|ограничени\w*|фильтр\w*|цензур\w*|лимит\w*|политик\w*)" + B,
    B + r"(режим разработчика|без цензуры|без ограничений|без фильтров|ты теперь свободен|взлом ограничений)" + B,
]

RULES = {
    "porn_word": PORN_WORD, "porn_compact": PORN_COMPACT,
    "gore_word": GORE_WORD, "gore_compact": GORE_COMPACT,
    "minor_word": MINOR_WORD, "csam_direct_word": CSAM_DIRECT_WORD, "sexual_word": SEXUAL_WORD,
    "jailbreak_word": JAILBREAK_WORD,
}

if __name__ == "__main__":
    import re
    for k, v in RULES.items():
        for p in v:
            re.compile(p, re.I)  # проверка, что регулярки корректны
    safety.encrypt_rules(RULES)
    safety._load()
    print("policy.enc собран; подпись:", "OK" if not safety.TAMPERED else "ОШИБКА: " + safety.TAMPER_REASON)
