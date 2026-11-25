"""Eight genuinely different design engines. Each carries its own image-prompt language, colour grading, typography
treatment and text-zone logic - they are not colour variants of one template.

``visual_source``: "photo" (needs an image model) or "typographic" (honestly drawn by the app; no AI artwork claimed)."""
from __future__ import annotations

NEGATIVE_BASE = ("text, letters, words, typography, watermark, logo, signature, caption, frame, border, "
                 "deformed, blurry, low quality, jpeg artifacts, oversaturated, extra fingers, bad hands, "
                 "disfigured face, crowd, face of Jesus, depiction of God")

STYLES = {
    "cinematic": {
        "name": "Cinematic photography", "treatment": "cinematic", "visual_source": "photo",
        "summary": "Realistic environment, dramatic light, deep shadow, film-poster composition, one strong focal subject.",
        "medium": "cinematic photograph, anamorphic lens, shallow depth of field, film still",
        "finish": "dramatic atmosphere, volumetric light, high dynamic range, sharp focus, 35mm film grain",
        "negative": "illustration, painting, 3d render, cartoon",
        "grade": {"contrast": 1.18, "saturation": 0.92, "brightness": 0.96, "tint": (18, 52, 70), "tint_k": 0.10,
                  "vignette": 0.42, "grain": 0.025},
        "zones": {"tall": "lower-left", "portrait": "lower-left", "square": "lower-left", "wide": "left-half"},
        "lighting": ["golden hour backlight breaking through storm clouds", "low moonlight with cold blue rim light",
                     "single shaft of light through haze at dusk"],
    },
    "biblical": {
        "name": "Biblical cinematic", "treatment": "biblical", "visual_source": "photo",
        "summary": "Ancient landscapes and symbolic scenes: desert, stone, olive trees, shepherds' hills. Respectful, never a face of God.",
        "medium": "cinematic photograph of an ancient Middle Eastern landscape, natural textures, timeless",
        "finish": "epic scale, warm earth tones, soft atmospheric haze, ultra detailed, reverent mood",
        "negative": "modern buildings, cars, cartoon, 3d render, depiction of Jesus, halo",
        "grade": {"contrast": 1.10, "saturation": 0.88, "brightness": 1.0, "tint": (120, 80, 30), "tint_k": 0.12,
                  "vignette": 0.38, "grain": 0.02},
        "zones": {"tall": "lower-center", "portrait": "lower-center", "square": "lower-center", "wide": "left-half"},
        "lighting": ["warm sunrise raking across the hills", "last light of sunset on limestone",
                     "soft dawn mist over the valley"],
    },
    "editorial": {
        "name": "Premium editorial", "treatment": "editorial", "visual_source": "photo",
        "summary": "Magazine layout: refined photography, serif headline, hairline rules, generous spacing, restrained palette.",
        "medium": "editorial photograph, clean minimal composition, natural materials, magazine cover quality",
        "finish": "soft natural light, muted refined colour palette, crisp detail, elegant negative space",
        "negative": "cartoon, 3d render, harsh flash, clutter",
        "grade": {"contrast": 1.06, "saturation": 0.80, "brightness": 1.0, "tint": (210, 200, 185), "tint_k": 0.06,
                  "vignette": 0.22, "grain": 0.012},
        "zones": {"tall": "upper-left", "portrait": "upper-left", "square": "upper-left", "wide": "left-half"},
        "lighting": ["soft diffused window light", "overcast daylight, gentle shadows", "clean morning light"],
    },
    "emotional": {
        "name": "Emotional storytelling", "treatment": "emotional", "visual_source": "photo",
        "summary": "A human moment: prayer, weariness, relief. Figures seen from behind or in silhouette for authenticity.",
        "medium": "documentary photograph, candid human moment, authentic emotion, natural light",
        "finish": "intimate, warm, true-to-life colour, shallow depth of field, honest and relatable",
        "negative": "stock photo smile, posed, cartoon, 3d render, visible face",
        "grade": {"contrast": 1.08, "saturation": 0.95, "brightness": 1.0, "tint": (255, 190, 120), "tint_k": 0.07,
                  "vignette": 0.30, "grain": 0.03},
        "zones": {"tall": "lower-center", "portrait": "lower-center", "square": "center", "wide": "left-half"},
        "lighting": ["warm late-afternoon window light", "soft blue hour light with warm practicals",
                     "early morning light on a quiet street"],
        "human": True,
    },
    "fineart": {
        "name": "Fine art", "treatment": "fineart", "visual_source": "photo",
        "summary": "Painterly, symbolic imagery with an artistic point of view; large italic serif type.",
        "medium": "oil painting, painterly brushwork, symbolic composition, museum quality fine art",
        "finish": "rich texture, harmonious colour, expressive light, masterful composition",
        "negative": "photograph, photorealistic, cartoon, anime, 3d render",
        "grade": {"contrast": 1.04, "saturation": 0.95, "brightness": 1.0, "tint": (60, 40, 90), "tint_k": 0.05,
                  "vignette": 0.30, "grain": 0.0},
        "zones": {"tall": "upper-center", "portrait": "upper-center", "square": "center", "wide": "left-half"},
        "lighting": ["chiaroscuro lighting", "luminous glow from a single source", "soft golden diffuse light"],
    },
    "minimal": {
        "name": "Minimalist typography", "treatment": "minimal", "visual_source": "typographic",
        "summary": "No artwork claimed: a tonal textured field, powerful type, intentional negative space.",
        "medium": "", "finish": "", "negative": "",
        "grade": {"contrast": 1.0, "saturation": 1.0, "brightness": 1.0, "tint": (0, 0, 0), "tint_k": 0.0,
                  "vignette": 0.15, "grain": 0.02},
        "zones": {"tall": "upper-left", "portrait": "upper-left", "square": "upper-left", "wide": "left-half"},
        "lighting": ["flat tonal field"],
    },
    "modern": {
        "name": "Modern Christian media", "treatment": "modern", "visual_source": "photo",
        "summary": "Contemporary photography, bold sans type with an accent bar, pill label, brand lockup.",
        "medium": "contemporary lifestyle photograph, modern urban or natural setting, clean premium social media look",
        "finish": "bright but moody grade, crisp, shallow depth of field, authentic and hopeful",
        "negative": "cartoon, 3d render, cheesy stock, clutter",
        "grade": {"contrast": 1.12, "saturation": 1.02, "brightness": 0.98, "tint": (40, 60, 120), "tint_k": 0.08,
                  "vignette": 0.32, "grain": 0.015},
        "zones": {"tall": "lower-left", "portrait": "lower-left", "square": "lower-left", "wide": "left-half"},
        "lighting": ["clean sunrise light, long shadows", "blue hour city light with warm accents",
                     "overcast soft light on greenery"],
    },
    "movie": {
        "name": "YouTube movie-poster", "treatment": "movie", "visual_source": "photo",
        "summary": "High-impact focal subject, teal-orange grade, huge condensed headline, thumbnail-ready contrast.",
        "medium": "movie poster key art photograph, dramatic hero subject, high contrast, teal and orange cinematic grade",
        "finish": "intense atmosphere, sharp focus, epic scale, blockbuster lighting, high impact",
        "negative": "illustration, cartoon, flat, low contrast",
        "grade": {"contrast": 1.28, "saturation": 1.08, "brightness": 0.94, "tint": (0, 70, 90), "tint_k": 0.14,
                  "vignette": 0.52, "grain": 0.02},
        "zones": {"tall": "lower-center", "portrait": "lower-center", "square": "lower-center", "wide": "left-half"},
        "lighting": ["hard rim light with orange backlight", "stormy sky lit by a distant glow",
                     "dramatic top light with deep shadow"],
    },
}

# Default trio for "three distinct concepts": photographic, editorial, symbolic/artistic.
DEFAULT_TRIO = ["cinematic", "editorial", "fineart"]

# Emotional themes the brief can name (same names as themes.py so legacy cards stay compatible).
PALETTES = {
    "warfare": [("storm grey, ember orange and deep black", "#F28C38"), ("slate blue and cold steel with a warm break of light", "#F2B84B"),
                ("charcoal and burnt crimson", "#E5533D")],
    "spirit": [("deep indigo and rising gold", "#FFC857"), ("midnight violet with warm firelight", "#FF9F43"),
               ("ash grey and glowing amber", "#FFB703")],
    "grace": [("warm gold and soft cream", "#F6D186"), ("honey amber and pale sky", "#FFE08A"),
              ("rose gold and soft ivory", "#F4C7A1")],
    "peace": [("soft teal and misty blue", "#9EE6F0"), ("sage green and pale morning grey", "#CFE8C8"),
              ("calm navy and silver", "#BCD7F2")],
    "hope": [("sunrise orange to violet", "#FFD08A"), ("pale gold and clear blue", "#FFE29A"),
             ("peach and soft lavender", "#FFC9A8")],
    "faith": [("deep midnight blue and starlight silver", "#BFD7FF"), ("navy and warm lantern gold", "#FFD98A"),
              ("indigo and soft white", "#E6EEFF")],
    "love": [("warm rose and candle gold", "#FFC1D0"), ("deep burgundy and soft peach", "#FFD6C2"),
             ("blush pink and cream", "#FFD9E2")],
    "strength": [("cold steel blue and granite grey", "#CFE8F4"), ("earth brown and dawn gold", "#F4C97A"),
                 ("deep green and stone", "#CDE6C4")],
    "growth": [("fresh green and morning gold", "#C8F08A"), ("deep forest green and dew silver", "#D7F2C4"),
               ("olive and warm sunlight", "#E8E39A")],
}

# Fallback visual metaphors when the model does not supply usable ones: (subject, environment)
METAPHORS = {
    "warfare": [("a lone figure kneeling in prayer, seen from behind", "a windswept ridge under breaking storm clouds"),
                ("an ancient stone watchtower standing firm", "dark hills with one beam of light piercing the clouds"),
                ("a worn sword and shield resting on weathered stone", "first light through drifting smoke")],
    "spirit": [("sparks of fire rising into the night", "a dark hillside under a vast sky"),
               ("an open sail filling with wind", "a calm sea at first light"),
               ("a single flame in an ancient clay lamp", "a stone room with golden light")],
    "grace": [("an open hand receiving light", "a quiet field at golden hour"),
              ("a wooden door ajar with warm light spilling out", "a quiet stone path"),
              ("a table set with bread and a lamp", "a simple room, warm evening")],
    "peace": [("a still lake reflecting the sky", "misty mountains at dawn"),
              ("a lone boat at rest", "glassy water, soft light"),
              ("a quiet path through tall grass", "a gentle hillside, early morning")],
    "hope": [("a sunrise breaking over distant mountains", "a long road leading to the light"),
             ("a small green shoot through cracked earth", "soft morning light"),
             ("a person standing at a window facing the dawn, seen from behind", "a quiet room, first light")],
    "faith": [("a lone traveller on a path under a vast starry sky", "open hills at night"),
              ("a lantern held up in the dark", "a narrow stone path"),
              ("stepping stones across a dark river", "mist and moonlight")],
    "love": [("two hands held together", "warm soft light"),
             ("an empty chair beside a lit candle", "a quiet home at dusk"),
             ("a shepherd's staff leaning on an olive tree", "warm hills at sunset")],
    "strength": [("a mountain peak above the storm clouds", "cold clear light"),
                 ("an old oak standing firm in a storm", "a windswept hillside"),
                 ("a figure climbing a steep trail, seen from behind", "rocky ridge, rising sun")],
    "growth": [("a young tree in morning light", "dew on green leaves"),
               ("a seedling and a watering can", "a quiet garden at sunrise"),
               ("a vineyard row stretching to the hills", "warm afternoon light")],
}

HUMAN_WORDS = ("person", "man", "woman", "figure", "child", "traveller", "traveler", "shepherd", "hands", "hand")


def style_ids() -> list:
    return list(STYLES)


def public_styles() -> list:
    return [{"id": k, "name": v["name"], "summary": v["summary"], "visual_source": v["visual_source"]}
            for k, v in STYLES.items()]
