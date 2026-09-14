"""공유 코드의 앞자리에 쓰이는 단어 목록.

고른 기준:
  - 소문자 ASCII 만. 한국어 사용자도 자판 전환 없이 입력할 수 있어야 한다.
  - 3~9자. 전화로 불러줄 수 있을 만큼 짧고 발음이 갈리지 않는 것.
  - 뜻이 중립적인 것 (지명 · 자연물). 오해를 살 단어는 넣지 않는다.

목록이 길수록 코드 공간이 넓어진다. 단어를 추가하는 것은 안전하지만,
**빼거나 순서를 바꾸는 것은 안 된다** — 이미 발급된 코드가 깨지지는 않으나
(코드는 문자열로 저장된다) 재현성 테스트가 흔들린다.
"""

CITIES: tuple[str, ...] = (
    "oslo", "bergen", "tromso", "malmo", "lund", "aarhus", "odense", "tampere",
    "turku", "riga", "vilnius", "tallinn", "helsinki", "reykjavik", "dublin",
    "cork", "galway", "belfast", "cardiff", "bristol", "leeds", "york", "oxford",
    "bath", "dover", "berlin", "munich", "hamburg", "bremen", "zurich", "geneva",
    "bern", "vienna", "prague", "warsaw", "krakow", "gdansk", "brno", "sofia",
    "athens", "patras", "milan", "turin", "naples", "venice", "verona", "genoa",
    "rome", "pisa", "siena", "madrid", "sevilla", "malaga", "bilbao", "valencia",
    "porto", "lisbon", "braga", "lyon", "nantes", "rennes", "tours", "dijon",
    "nice", "cannes", "toulon", "ghent", "bruges", "leuven", "utrecht", "leiden",
    "delft", "haarlem", "tokyo", "kyoto", "osaka", "nagoya", "sapporo", "sendai",
    "kobe", "seoul", "busan", "daegu", "incheon", "gwangju", "jeju", "taipei",
    "tainan", "hanoi", "manila", "cebu", "jakarta", "bandung", "bangkok",
    "chiangmai", "penang", "colombo", "pokhara", "thimphu", "ankara", "izmir",
    "bursa", "tbilisi", "yerevan", "cairo", "luxor", "tunis", "rabat", "fez",
    "dakar", "accra", "nairobi", "kampala", "lusaka", "durban", "pretoria",
    "maputo", "quito", "lima", "cusco", "bogota", "medellin", "brasilia",
    "recife", "natal", "salvador", "santos", "rosario", "mendoza", "valdivia",
    "santiago", "asuncion", "panama", "havana", "kingston", "nassau", "merida",
    "oaxaca", "puebla", "cancun", "denver", "boston", "austin", "dallas",
    "seattle", "portland", "phoenix", "atlanta", "orlando", "tampa", "tucson",
    "omaha", "tulsa", "madison", "dayton", "toledo", "salem", "eugene", "tacoma",
    "spokane", "boise", "juneau", "ottawa", "toronto", "calgary", "regina",
    "halifax", "victoria", "sydney", "perth", "hobart", "darwin", "cairns",
    "auckland", "dunedin", "nelson",
)

NATURE: tuple[str, ...] = (
    "amber", "anchor", "arbor", "arrow", "aspen", "autumn", "azure", "basil",
    "beacon", "birch", "blossom", "bramble", "breeze", "bridge", "brook",
    "canyon", "cedar", "cinder", "cliff", "clover", "cobalt", "comet", "copper",
    "coral", "cove", "crater", "crescent", "crystal", "dahlia", "daisy", "dawn",
    "delta", "dune", "dusk", "ember", "fable", "falcon", "fathom", "fennel",
    "fern", "fjord", "flint", "forest", "fountain", "galaxy", "garnet",
    "glacier", "glade", "granite", "grove", "harbor", "harvest", "hazel",
    "heather", "hollow", "horizon", "indigo", "ivory", "jasmine", "jasper",
    "juniper", "kelp", "lagoon", "lantern", "larch", "lattice", "laurel",
    "lichen", "lilac", "linden", "lotus", "lumen", "lupine", "magnolia",
    "maple", "marble", "meadow", "mesa", "mineral", "mirage", "mist", "moss",
    "nectar", "nimbus", "nova", "oasis", "obsidian", "olive", "onyx", "opal",
    "orchid", "osprey", "otter", "pebble", "petal", "pewter", "pine", "plateau",
    "pollen", "poplar", "prairie", "quartz", "quill", "ridge", "ripple",
    "river", "rowan", "saffron", "sage", "sandbar", "sapphire", "savanna",
    "sequoia", "shale", "shore", "silver", "slate", "solstice", "sparrow",
    "spruce", "summit", "sunset", "tamarack", "thicket", "thistle", "tide",
    "timber", "topaz", "tundra", "valley", "velvet", "verdant", "vertex",
    "willow", "wisteria", "zenith", "zephyr",
)

WORDS: tuple[str, ...] = CITIES + NATURE
