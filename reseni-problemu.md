---
title: Řešení problémů
nav_order: 3
---

# Řešení problémů

{: .warning }
> **Prvním krokem je vždy restart ArcGIS Pro.**
> Většina potíží – hlavně **hned po importu nebo aktualizaci toolboxu** – zmizí po úplném zavření a novém spuštění ArcGIS Pro.

## Nejčastější potíže

### Nástroj se nespustí nebo hlásí chybu hned po importu
Restartuj ArcGIS Pro. Toolbox se po přidání nemusí načíst celý.

### U toolboxu je červený vykřičník
Otevři ho – nástroj ukáže chybovou zprávu. Nejčastěji chybí pomocný `.py` soubor ve stejné složce jako `.pyt` (zip nebyl rozbalený celý) nebo je v paměti stará verze modulu. Zkontroluj soubory a restartuj ArcGIS Pro.

### Chybí parametry nebo se nástroj chová „postaru“
ArcGIS drží v paměti starší verzi. Odeber toolbox z projektu, restartuj ArcGIS Pro a přidej ho znovu.

### V seznamu layoutů / prvků nic není
Nástroje pro layout pracují s **aktuálně otevřeným projektem**. Otevři projekt, který layout obsahuje, a dialog nástroje zavři a otevři znovu.

### Chyba v Pythonu (traceback v okně Messages)
Zkontroluj, že všechny soubory toolboxu jsou ve stejné složce a že máš ArcGIS Pro 3.x. Pokud chyba přetrvává i po restartu, nahlas ji (viz níže).

## Nahlášení chyby

Pokud problém přetrvává **i po restartu**, založ [issue v repozitáři](https://github.com/fifi1ous/arcgis-pro-toolboxes/issues) a přilož:

- název toolboxu a nástroje,
- verzi ArcGIS Pro,
- text chyby z okna **Messages** (nebo snímek obrazovky),
- stručný popis vstupních dat / layoutu.
