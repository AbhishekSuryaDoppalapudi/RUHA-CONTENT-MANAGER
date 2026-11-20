"""Which words of a caption get the highlight: meaningful words, not the longest ones, and never filler.

Priority (highest first):
  100  names of God: God, Jesus, Christ, Lord, Spirit, Messiah, Saviour ... (and "Father", "Son", "Holy", "Ghost" when the
       speaker writes them with a capital in the middle of a sentence - that is how the transcript marks God)
   90  Bible book names and verse numbers: "John 3:16", "Romans 8", "chapter 3 verse 16", "Psalms"
   80  words the AI marked as key for that clip (only words that really occur in it)
   60  strong nouns and verbs: grace, faith, forgive, surrender, redeem, cross ...
   10-25  any other content word (5+ letters 25, 4 letters 10; length is not meaning)
    0  filler: articles, pronouns, auxiliaries, prepositions, "just", "really", "you know", -ly adverbs ...
A word below MIN_SCORE is never highlighted, so a phrase made only of filler gets no highlight at all.
"""
from __future__ import annotations

import re

MIN_SCORE = 10
TRIM = ".,!?;:'\"“”‘’()[]…—–-"

NAMES = {"god", "jesus", "christ", "lord", "spirit", "messiah", "saviour", "savior", "yahweh", "jehovah", "emmanuel",
         "immanuel", "almighty", "yeshua", "abba", "trinity"}
GOD_WHEN_CAPITALISED = {"father", "son", "holy", "ghost", "word", "king", "creator", "shepherd"}

# books safe to treat as a book without a chapter number after them (no common word or name shares the spelling)
BOOKS_ALONE = ("genesis exodus leviticus deuteronomy psalm psalms proverbs ecclesiastes isaiah jeremiah lamentations ezekiel "
               "habakkuk zephaniah malachi romans corinthians galatians ephesians philippians colossians thessalonians "
               "hebrews revelation").split()
# books that are also ordinary words or first names: a book only with a chapter number after it, or after "book of"
BOOKS_NEED_NUMBER = ("numbers joshua judges ruth samuel kings chronicles ezra nehemiah esther job song solomon daniel hosea joel amos "
                     "obadiah jonah micah nahum haggai zechariah matthew mark luke john acts timothy titus philemon james peter "
                     "jude").split()
BOOK_NAMES = set(BOOKS_ALONE) | set(BOOKS_NEED_NUMBER)
BOOK_PATTERN = "|".join(sorted(BOOK_NAMES | {"song of solomon", "song of songs"}, key=len, reverse=True))

STRONG = set("""grace mercy faith hope love fear forgive forgiven forgiveness repent repentance sin sins sinner salvation saved
cross blood resurrection redeem redeemed redemption glory truth peace joy prayer pray worship surrender obey obedience believe
trust heal healing healed miracle covenant promise promises kingdom anointing anointed sacrifice freedom free victory battle war
enemy darkness light fire power strength purpose calling destiny temptation shame pain suffering comfort church gospel disciple
disciples blessing blessed curse heaven hell eternal eternity life death soul heart righteous righteousness wisdom humble humility
patience persevere endure courage fearless identity forgiving restore restoration deliver deliverance bondage chains rise rebuild
sacred thankful grateful gratitude praise sing cry weep tears broken wounded lost found loving""".split())

# verbs and nouns of the faith vocabulary that inflect: love -> loved, loving, lovers; heal -> healed, healing ...
INFLECT = ("love heal pray trust save bless believe forgive repent redeem worship obey rise sing cry weep restore deliver praise "
           "endure comfort sacrifice promise anoint grieve").split()


def _forms(root: str) -> set:
    base = root[:-1] if root.endswith("e") else root
    return {root, root + "s", root + "d", root + "r", root + "rs", root + "es"} | {base + x for x in ("ed", "er", "ers", "ing", "ings", "ness", "ful")}


STRONG_FORMS = set(STRONG) | set().union(*(_forms(r) for r in INFLECT))

FILLER = set("""a an the and or but nor so yet if then than that this these those there here it its it's he she they them their his
her him hers we us our ours you your yours i me my mine who whom whose what which when where why how all any each every both few
more most other some such no not only own same too very can could will would shall should may might must do does did done doing
have has had having be is are was were been being am to of in on at by for with about against between into through during before
after above below from up down out off over under again further once also just really actually basically literally like okay ok
um uh yeah yes right now well still even maybe perhaps kind sort gonna gotta wanna going goin know knew mean means see saw say said
says saying think thought thing things something anything everything nothing someone anyone everyone way lot bit get gets got
getting let lets make makes made come comes came coming go goes went one two three much many quite rather already always never
ever often sometimes usually really oh ah hey look listen because while until unless though although whether either neither
since upon within without across around along among toward towards onto another others else instead anyway however therefore
thus hence per via chapter chapters verse verses""".split())
# whole-word "-ly" words that are real content (nouns/adjectives), not adverbs
LY_OK = {"holy", "family", "lonely", "lovely", "friendly", "deadly", "heavenly", "godly", "daily"}

# function words of Hindi / Telugu / other Indian-language captions (no part-of-speech tagger is available for them)
INDIC_FILLER = set("""है हैं था थी थे का की के को से में पर और भी यह वह एक कि जो तो ने हो ही नहीं या इस उस कोई अब जब तब यहाँ वहाँ
और ఈ ఆ ఒక మరియు కూడా అని లో కు ను యొక్క ఉంది ఉన్నాయి ఉన్న కానీ అయితే నుండి కోసం వల్ల తో గా వారు మేము మీరు నేను అది ఇది""".split())

ORDINALS = {"1", "2", "3", "first", "second", "third"}
_PUNCT = re.compile(r"[^\w']+", re.U)


def norm(word: str) -> str:
    """Lower-case letters only: punctuation, possessive 's and curly quotes removed."""
    w = (word or "").strip(TRIM).lower().replace("’", "'")
    if w.endswith("'s"):
        w = w[:-2]
    return _PUNCT.sub("", w)


def stem(word: str) -> str:
    return norm(word)[:5]


def is_latin(word: str) -> bool:
    return all(ord(c) < 0x0250 or 0x2000 <= ord(c) < 0x2100 for c in word or "")


def is_filler(word: str) -> bool:
    w = norm(word)
    if not w:
        return True
    if not is_latin(w):
        return w in INDIC_FILLER or len(w) <= 1
    if "'" in w:                                          # he'll, don't, I'm, we're: a contraction is never a highlight
        return True
    if w in FILLER:
        return True
    if w in NAMES or w in STRONG or w in BOOK_NAMES or w.isdigit():
        return False
    if len(w) > 4 and w.endswith("ly") and w not in LY_OK:
        return True
    return len(w) <= 2


def is_strong(w: str) -> bool:
    """A strong noun/verb of the faith vocabulary, in any common form: love, loved, loving; heal, healed, healing; pray, prayers."""
    return w in STRONG_FORMS


def is_number_token(word: str) -> bool:
    """3, 16, 3:16, 3-5 - a chapter / verse number."""
    return bool(re.fullmatch(r"\d{1,3}(?:[:.\-–]\d{1,3})?", (word or "").strip(TRIM)))


# ------------------------------------------------------------------ Bible references
def reference_spans(words: list) -> list:
    """[(i, j)] ranges of ``words`` (strings) that are one Bible reference: "John 3:16", "1 John 4", "Romans chapter 8 verse 28",
    "the book of Ruth", a lone "Psalms". Book names that are also ordinary words or first names need a number after them
    (or "book of" before them)."""
    toks = [norm(w) for w in words]
    n, spans, i = len(words), [], 0
    while i < n:
        k = i
        if toks[i] in ORDINALS and i + 1 < n and (toks[i + 1] in BOOK_NAMES):          # "1 John", "second Corinthians"
            k = i + 1
        if toks[k:k + 3] in (["song", "of", "solomon"], ["song", "of", "songs"]):
            book, e_book = "song", k + 3
        elif toks[k] in BOOK_NAMES:
            book, e_book = toks[k], k + 1
        else:
            i += 1
            continue
        e, had_number = e_book, False
        while e < n and (toks[e] in {"chapter", "chapters", "verse", "verses"} or is_number_token(words[e])):
            had_number = had_number or is_number_token(words[e])
            e += 1
        while e > e_book and not is_number_token(words[e - 1]):                         # do not swallow a trailing "chapter"
            e -= 1
        after_book_of = k >= 2 and toks[k - 2:k] == ["book", "of"]
        if had_number or book in BOOKS_ALONE or after_book_of or k > i:
            spans.append((i, e))
        i = max(e, e_book, i + 1)
    return spans


def number_after_reference(words: list) -> set:
    """Indexes of verse numbers in "chapter 3 verse 16" / "verse 16" with no book name."""
    toks = [norm(w) for w in words]
    out = set()
    for i, t in enumerate(toks):
        if t in {"chapter", "verse", "verses"} and i + 1 < len(words) and is_number_token(words[i + 1]):
            out.add(i + 1)
    return out


# ------------------------------------------------------------------ scoring
def score(word: str, *, llm: set | None = None, ref: bool = False, mid_capital: bool = False) -> int:
    """How much a word deserves the highlight (see the module docstring). 0 = never."""
    w = norm(word)
    if ref:
        return 90
    if not w or is_filler(word):
        return 0
    if w in NAMES or (mid_capital and w in GOD_WHEN_CAPITALISED):
        return 100
    if w in BOOK_NAMES and w in BOOKS_ALONE:
        return 90
    if llm and (w in llm or (len(w) >= 5 and w[:5] in {k[:5] for k in llm if len(k) >= 5})):
        return 80
    if is_strong(w):
        return 60
    if not is_latin(w):
        return 25                                         # Indian-language word that is not a function word
    if w.isdigit():
        return 0
    return 25 if len(w) >= 5 else 10 if len(w) == 4 else 0      # length is not meaning: no bonus for long words


def grounded_keys(keys, text: str, limit: int = 4) -> list:
    """The AI's key words for a clip, kept only when they are real words of that clip (compared on their first five letters),
    are not filler, and are not repeats. The model may not invent a word to highlight."""
    have = {stem(w) for w in re.split(r"\s+", text or "") if norm(w)}
    out, seen = [], set()
    for k in ([keys] if isinstance(keys, str) else keys or []):
        for part in str(k).split():
            n = norm(part)
            if n and not is_filler(part) and stem(part) in have and n not in seen:
                seen.add(n)
                out.append(n)
    return out[:limit]


def auto_keys(text: str, limit: int = 3) -> list:
    """Key words picked from the clip's own text by the same rules (for plans made without a model)."""
    words = [w for w in re.split(r"\s+", text or "") if w]
    sc = word_scores(words)
    best = sorted((i for i, s in enumerate(sc) if s >= 25), key=lambda i: (-sc[i], i))
    out = []
    for i in best:
        n = norm(words[i])
        if n and n not in out and not is_number_token(words[i]):
            out.append(n)
        if len(out) >= limit:
            break
    return out


def llm_keys(keys) -> set:
    """The AI's key words as a lookup set of normalised words."""
    out = set()
    for k in ([keys] if isinstance(keys, str) else keys or []):
        for part in str(k).split():
            n = norm(part)
            if n and not is_filler(part):
                out.add(n)
    return out


def word_scores(words: list, keys=None, first_in_sentence: bool = True) -> list:
    """A score for every word of a phrase (a list of strings), in order.

    Bible references score 90 for every word of the reference; a capital in the middle of a sentence marks "Father", "Son"
    and "Holy" as names of God."""
    llm = llm_keys(keys)
    refs = {i for a, b in reference_spans(words) for i in range(a, b)} | number_after_reference(words)
    out = []
    for i, w in enumerate(words):
        raw = (w or "").strip(TRIM)
        cap = bool(raw[:1].isupper()) and not (i == 0 and first_in_sentence)
        out.append(score(w, llm=llm, ref=i in refs, mid_capital=cap))
    return out


def pick(words: list, keys=None, n: int = 1, first_in_sentence: bool = True) -> list:
    """Indexes of the ``n`` words to highlight, best first. Fewer (or none) when the phrase has nothing meaningful.
    Ties go to the later word, which is what the sentence lands on."""
    sc = word_scores(words, keys, first_in_sentence)
    order = sorted((i for i, s in enumerate(sc) if s >= MIN_SCORE), key=lambda i: (-sc[i], -i))
    return order[:n]
