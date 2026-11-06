# Toolboxy pro ArcGIS Pro

Sbírka Python toolboxů (`.pyt`) pro ArcGIS Pro.

📖 **Dokumentace a stažení:** https://fifi1ous.github.io/arcgis-pro-toolboxes/

> ⚠️ **Když něco nefunguje tak, jak má, restartuj ArcGIS Pro** – hlavně po importu nebo aktualizaci toolboxu.

## Toolboxy

### Layout

| Toolbox | Co dělá |
|:--|:--|
| [Value-Scale Generator](toolboxy/layout/ValueScale) | Hodnotová stupnice pro mapy s proporcionálními symboly |
| [Segmented Legend](toolboxy/layout/SegmentedLegend) | Legenda ve tvaru výsečového / prstencového / polygonového grafu |
| [Legend Formatter](toolboxy/layout/LegendFormatter) | Přenese rozvržení jedné legendy (převedené na grafiku) na jinou |

## Struktura repozitáře

```
.
├── toolboxy/                   # zdrojové soubory toolboxů
│   └── layout/
│       ├── ValueScale/         # jedna složka = jeden toolbox = jeden zip ke stažení
│       ├── SegmentedLegend/
│       └── LegendFormatter/
├── docs/                       # web (GitHub Pages, Jekyll + Just the Docs)
│   ├── _config.yml
│   ├── index.md                # rozcestník
│   ├── instalace.md
│   ├── reseni-problemu.md
│   ├── layout/
│   │   ├── index.md            # sekce „Layout“
│   │   ├── <toolbox>.md        # stránka toolboxu
│   │   └── img/                # obrázky
│   └── SABLONA_TOOLBOXU.md     # šablona nové stránky (nepublikuje se)
└── .github/workflows/pages.yml # zabalí toolboxy do zipů a nasadí web
```

Zipy ke stažení (`docs/stazeni/*.zip`) se **necommitují** – vytváří je GitHub Actions při každém pushi do `main` z obsahu složek v `toolboxy/`.

## Zapnutí GitHub Pages

**Settings → Pages → Build and deployment → Source: GitHub Actions.** Po prvním pushi (nebo ručním spuštění workflow v záložce *Actions*) je web dostupný na adrese výše.

## Přidání nového toolboxu

1. Soubory toolboxu dej do nové složky `toolboxy/<sekce>/<NazevToolboxu>/` (sekce `layout` nebo `algoritmy`).
2. Zkopíruj `docs/SABLONA_TOOLBOXU.md` do `docs/<sekce>/<nazev-toolboxu>.md`, vyplň ji a nastav v hlavičce `parent` a odkaz ke stažení `../stazeni/<NazevToolboxu>.zip`.
3. Obrázky ulož do `docs/<sekce>/img/`.
4. Přidej řádek do tabulky v `docs/index.md` (a sem do README).

### První toolbox v sekci „Algoritmy a výpočty“

Sekce se na webu zatím nezobrazuje, protože v ní nic není. Až přibude první toolbox:

1. Vytvoř `docs/algoritmy/index.md`:
   ```markdown
   ---
   title: Algoritmy a výpočty
   nav_order: 4
   has_children: true
   ---

   # Algoritmy a výpočty

   Nástroje, které počítají nebo zpracovávají data.
   ```
2. V `docs/layout/index.md` změň `nav_order` na `5`, ať je Layout v menu až za algoritmy.
3. Do `docs/index.md` přidej nad sekci Layout sekci `## Algoritmy a výpočty` se stejnou tabulkou.
4. Stránky toolboxů mají v hlavičce `parent: Algoritmy a výpočty`.

## Lokální náhled (volitelné)

```bash
cd docs
bundle install
bundle exec jekyll serve
```

Web běží na `http://localhost:4000/arcgis-pro-toolboxes/` (bez zipů ke stažení – ty vznikají až v GitHub Actions).
