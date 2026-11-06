---
title: Instalace a import
nav_order: 2
---

# Instalace a import toolboxu

## Požadavky

- ArcGIS Pro 3.x (Segmented Legend vyžaduje 3.0+)
- Žádné další knihovny ani extenze – stačí Python prostředí, které je součástí ArcGIS Pro.

## Stažení

Každý toolbox má na své stránce tlačítko **Stáhnout (.zip)**. Zip obsahuje složku se **všemi soubory**, které toolbox potřebuje.

1. Stáhni zip a **rozbal ho** do trvalého umístění (např. `Dokumenty\ArcGIS\Toolboxy\`). Neotvírej toolbox přímo ze zipu ani ze složky Stažené soubory, kterou průběžně mažeš.
2. Soubory ve složce nech pohromadě – některé toolboxy (např. Value-Scale Generator) potřebují pomocné `.py` soubory **ve stejné složce** jako `.pyt`.

## Import do ArcGIS Pro

1. Otevři projekt v ArcGIS Pro.
2. V panelu **Catalog** klikni pravým tlačítkem na **Toolboxes** → **Add Toolbox**.
3. Vyber soubor `.pyt` z rozbalené složky a potvrď.
4. **Restartuj ArcGIS Pro.**
5. Toolbox se objeví v Catalogu; nástroj spustíš dvojklikem jako jakýkoli geoprocessingový nástroj.

{: .warning }
> **Po importu toolboxu restartuj ArcGIS Pro.**
> Zavři celý ArcGIS Pro (ne jen projekt) a znovu ho spusť. Bez restartu se můžou načítat staré verze nástrojů, chybět parametry, u toolboxu se může objevit červený vykřičník nebo se nástroj nespustí vůbec. Totéž platí po **aktualizaci** toolboxu na novou verzi.

## Aktualizace na novou verzi

1. Zavři ArcGIS Pro.
2. Stáhni nový zip a nahraď staré soubory novými (stejné umístění, **všechny** soubory ze zipu).
3. Spusť ArcGIS Pro znovu.

{: .tip }
Pokud po aktualizaci vidíš staré chování, odeber toolbox z projektu (**Remove**), restartuj ArcGIS Pro a přidej ho znovu.
