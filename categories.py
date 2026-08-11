from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Category:
    key: str
    name: str
    url: str
    group: str
    is_group: bool = False


@dataclass(frozen=True)
class CategoryGroup:
    key: str
    name: str
    icon: str
    url: str


GROUPS: dict[str, CategoryGroup] = {
    "auto": CategoryGroup("auto", "Auto, Rad & Boot", "🚗", "https://www.kleinanzeigen.de/s-auto-rad-boot/c210"),
    "immobilien": CategoryGroup("immobilien", "Immobilien", "🏠", "https://www.kleinanzeigen.de/s-immobilien/c195"),
    "haus": CategoryGroup("haus", "Haus & Garten", "🛋", "https://www.kleinanzeigen.de/s-haus-garten/c80"),
    "mode": CategoryGroup("mode", "Mode & Beauty", "👕", "https://www.kleinanzeigen.de/s-mode-beauty/c153"),
    "elektronik": CategoryGroup("elektronik", "Elektronik", "💻", "https://www.kleinanzeigen.de/s-multimedia-elektronik/c161"),
    "tiere": CategoryGroup("tiere", "Haustiere", "🐾", "https://www.kleinanzeigen.de/s-haustiere/c130"),
    "familie": CategoryGroup("familie", "Familie, Kind & Baby", "🧸", "https://www.kleinanzeigen.de/s-familie-kind-baby/c17"),
    "jobs": CategoryGroup("jobs", "Jobs", "💼", "https://www.kleinanzeigen.de/s-jobs/c102"),
    "freizeit": CategoryGroup("freizeit", "Freizeit, Hobby & Nachbarschaft", "🎨", "https://www.kleinanzeigen.de/s-freizeit-nachbarschaft/c185"),
    "musik": CategoryGroup("musik", "Musik, Filme & Bücher", "🎵", "https://www.kleinanzeigen.de/s-musik-film-buecher/c73"),
    "tickets": CategoryGroup("tickets", "Eintrittskarten & Tickets", "🎫", "https://www.kleinanzeigen.de/s-eintrittskarten-tickets/c231"),
    "services": CategoryGroup("services", "Dienstleistungen", "🛠", "https://www.kleinanzeigen.de/s-dienstleistungen/c297"),
    "free": CategoryGroup("free", "Verschenken & Tauschen", "🎁", "https://www.kleinanzeigen.de/s-zu-verschenken-tauschen/c272"),
    "kurse": CategoryGroup("kurse", "Unterricht & Kurse", "📚", "https://www.kleinanzeigen.de/s-unterricht-kurse/c235"),
    "hilfe": CategoryGroup("hilfe", "Nachbarschaftshilfe", "🤝", "https://www.kleinanzeigen.de/s-nachbarschaftshilfe/c400"),
}


def _c(key: str, name: str, url: str, group: str) -> Category:
    return Category(key=key, name=name, url=url, group=group)


CATEGORIES: dict[str, Category] = {}

# Root categories themselves are selectable too.
for _g in GROUPS.values():
    CATEGORIES[f"g_{_g.key}"] = Category(
        key=f"g_{_g.key}", name=f"Весь раздел: {_g.name}", url=_g.url, group=_g.key, is_group=True
    )

# Auto, Rad & Boot
for x in [
    _c("auto_autos", "Autos", "https://www.kleinanzeigen.de/s-autos/c216", "auto"),
    _c("auto_fahrraeder", "Fahrräder & Zubehör", "https://www.kleinanzeigen.de/s-fahrraeder/c217", "auto"),
    _c("auto_teile", "Autoteile & Reifen", "https://www.kleinanzeigen.de/s-autoteile-reifen/c223", "auto"),
    _c("auto_boote", "Boote & Bootszubehör", "https://www.kleinanzeigen.de/s-boote-bootszubehoer/c211", "auto"),
    _c("auto_motorrad", "Motorräder & Motorroller", "https://www.kleinanzeigen.de/s-motorraeder-roller/c305", "auto"),
    _c("auto_mototeile", "Motorradteile & Zubehör", "https://www.kleinanzeigen.de/s-motorraeder-roller-teile/c306", "auto"),
    _c("auto_nutz", "Nutzfahrzeuge & Anhänger", "https://www.kleinanzeigen.de/s-anhaenger-nutzfahrzeuge/c276", "auto"),
    _c("auto_reparatur", "Reparaturen & Dienstleistungen", "https://www.kleinanzeigen.de/s-reparaturen-dienstleistungen/c280", "auto"),
    _c("auto_wohn", "Wohnwagen & -mobile", "https://www.kleinanzeigen.de/s-wohnwagen-mobile/c220", "auto"),
    _c("auto_sonst", "Weiteres Auto, Rad & Boot", "https://www.kleinanzeigen.de/s-auto-rad-boot/sonstiges/c241", "auto"),
]: CATEGORIES[x.key] = x

# Immobilien
for x in [
    _c("im_neubau", "Neubauprojekte", "https://www.kleinanzeigen.de/s-neubauprojekte/c403", "immobilien"),
    _c("im_miete", "Mietwohnungen", "https://www.kleinanzeigen.de/s-wohnung-mieten/c203", "immobilien"),
    _c("im_haus_kauf", "Häuser zum Kauf", "https://www.kleinanzeigen.de/s-haus-kaufen/c208", "immobilien"),
    _c("im_wg", "Auf Zeit & WG", "https://www.kleinanzeigen.de/s-auf-zeit-wg/c199", "immobilien"),
    _c("im_container", "Container", "https://www.kleinanzeigen.de/s-container/c402", "immobilien"),
    _c("im_eigentum", "Eigentumswohnungen", "https://www.kleinanzeigen.de/s-wohnung-kaufen/c196", "immobilien"),
    _c("im_ferien", "Ferien- & Auslandsimmobilien", "https://www.kleinanzeigen.de/s-ferienwohnung-ferienhaus/c275", "immobilien"),
    _c("im_garage", "Garagen & Stellplätze", "https://www.kleinanzeigen.de/s-garage-lagerraum/c197", "immobilien"),
    _c("im_gewerbe", "Gewerbeimmobilien", "https://www.kleinanzeigen.de/s-gewerbeimmobilien/c277", "immobilien"),
    _c("im_grund", "Grundstücke & Gärten", "https://www.kleinanzeigen.de/s-grundstuecke-garten/c207", "immobilien"),
    _c("im_haus_miete", "Häuser zur Miete", "https://www.kleinanzeigen.de/s-haus-mieten/c205", "immobilien"),
    _c("im_umzug", "Umzug & Transport", "https://www.kleinanzeigen.de/s-umzug-transport/c238", "immobilien"),
    _c("im_sonst", "Weitere Immobilien", "https://www.kleinanzeigen.de/s-immobilien/sonstiges/c198", "immobilien"),
]: CATEGORIES[x.key] = x

# Haus & Garten
for x in [
    _c("hg_kueche", "Küche & Esszimmer", "https://www.kleinanzeigen.de/s-kueche-esszimmer/c86", "haus"),
    _c("hg_wohn", "Wohnzimmer", "https://www.kleinanzeigen.de/s-wohnzimmer/c88", "haus"),
    _c("hg_bad", "Badezimmer", "https://www.kleinanzeigen.de/s-badezimmer/c91", "haus"),
    _c("hg_buero", "Büro", "https://www.kleinanzeigen.de/s-bueromoebel/c93", "haus"),
    _c("hg_deko", "Dekoration", "https://www.kleinanzeigen.de/s-dekoration/c246", "haus"),
    _c("hg_service", "Dienstleistungen Haus & Garten", "https://www.kleinanzeigen.de/s-dienstleistungen-haus-garten/c239", "haus"),
    _c("hg_garten", "Gartenzubehör & Pflanzen", "https://www.kleinanzeigen.de/s-garten-pflanzen/c89", "haus"),
    _c("hg_textil", "Heimtextilien", "https://www.kleinanzeigen.de/s-heimtextilien/c90", "haus"),
    _c("hg_heimwerk", "Heimwerken", "https://www.kleinanzeigen.de/s-heimwerken/c84", "haus"),
    _c("hg_lampen", "Lampen & Licht", "https://www.kleinanzeigen.de/s-lampen-licht/c82", "haus"),
    _c("hg_schlaf", "Schlafzimmer", "https://www.kleinanzeigen.de/s-schlafzimmer/c81", "haus"),
    _c("hg_sonst", "Weiteres Haus & Garten", "https://www.kleinanzeigen.de/s-haus-garten/sonstiges/c87", "haus"),
]: CATEGORIES[x.key] = x

# Mode & Beauty
for x in [
    _c("mo_damen", "Damenbekleidung", "https://www.kleinanzeigen.de/s-kleidung-damen/c154", "mode"),
    _c("mo_herren", "Herrenbekleidung", "https://www.kleinanzeigen.de/s-kleidung-herren/c160", "mode"),
    _c("mo_beauty", "Beauty & Gesundheit", "https://www.kleinanzeigen.de/s-beauty-gesundheit/c224", "mode"),
    _c("mo_dschuhe", "Damenschuhe", "https://www.kleinanzeigen.de/s-schuhe-damen/c159", "mode"),
    _c("mo_hschuhe", "Herrenschuhe", "https://www.kleinanzeigen.de/s-schuhe-herren/c158", "mode"),
    _c("mo_access", "Taschen & Accessoires", "https://www.kleinanzeigen.de/s-accessoires-schmuck/c156", "mode"),
    _c("mo_uhren", "Uhren & Schmuck", "https://www.kleinanzeigen.de/s-uhren-schmuck/c157", "mode"),
    _c("mo_sonst", "Weiteres Mode & Beauty", "https://www.kleinanzeigen.de/s-mode-beauty/sonstiges/c155", "mode"),
]: CATEGORIES[x.key] = x

# Elektronik
for x in [
    _c("el_handy", "Handy & Telefon", "https://www.kleinanzeigen.de/s-handy-telekom/c173", "elektronik"),
    _c("el_haushalt", "Haushaltsgeräte", "https://www.kleinanzeigen.de/s-haushaltsgeraete/c176", "elektronik"),
    _c("el_audio", "Audio & Hifi", "https://www.kleinanzeigen.de/s-audio-hifi/c172", "elektronik"),
    _c("el_service", "Dienstleistungen Elektronik", "https://www.kleinanzeigen.de/s-dienstleistungen-edv/c226", "elektronik"),
    _c("el_foto", "Foto", "https://www.kleinanzeigen.de/s-foto/c245", "elektronik"),
    _c("el_konsolen", "Konsolen", "https://www.kleinanzeigen.de/s-konsolen/c279", "elektronik"),
    _c("el_notebooks", "Laptops & Notebooks", "https://www.kleinanzeigen.de/s-notebooks/c278", "elektronik"),
    _c("el_pcs", "PCs", "https://www.kleinanzeigen.de/s-pcs/c228", "elektronik"),
    _c("el_pcz", "PC-Zubehör & Software", "https://www.kleinanzeigen.de/s-pc-zubehoer-software/c225", "elektronik"),
    _c("el_tablets", "Tablets & Reader", "https://www.kleinanzeigen.de/s-tablets-reader/c285", "elektronik"),
    _c("el_tv", "TV & Video", "https://www.kleinanzeigen.de/s-tv-video/c175", "elektronik"),
    _c("el_games", "Videospiele", "https://www.kleinanzeigen.de/s-pc-videospiele/c227", "elektronik"),
    _c("el_wear", "Wearables", "https://www.kleinanzeigen.de/s-wearables/c405", "elektronik"),
    _c("el_wearz", "Wearables Zubehör", "https://www.kleinanzeigen.de/s-wearables-zubehor/c406", "elektronik"),
    _c("el_sonst", "Weitere Elektronik", "https://www.kleinanzeigen.de/s-multimedia-elektronik/sonstiges/c168", "elektronik"),
]: CATEGORIES[x.key] = x

# Haustiere
for x in [
    _c("ti_hunde", "Hunde", "https://www.kleinanzeigen.de/s-hunde/c134", "tiere"),
    _c("ti_katzen", "Katzen", "https://www.kleinanzeigen.de/s-katzen/c136", "tiere"),
    _c("ti_fische", "Fische", "https://www.kleinanzeigen.de/s-fische/c138", "tiere"),
    _c("ti_klein", "Kleintiere", "https://www.kleinanzeigen.de/s-kleintiere/c132", "tiere"),
    _c("ti_nutz", "Nutztiere", "https://www.kleinanzeigen.de/s-nutztiere/c135", "tiere"),
    _c("ti_pferde", "Pferde", "https://www.kleinanzeigen.de/s-pferde/c139", "tiere"),
    _c("ti_betreuung", "Tierbetreuung & Training", "https://www.kleinanzeigen.de/s-tierbetreuung-training/c133", "tiere"),
    _c("ti_vermisst", "Vermisste Tiere", "https://www.kleinanzeigen.de/s-vermisste-tiere/c283", "tiere"),
    _c("ti_voegel", "Vögel", "https://www.kleinanzeigen.de/s-vogel/c243", "tiere"),
    _c("ti_zubehoer", "Zubehör", "https://www.kleinanzeigen.de/s-zubehoer/c313", "tiere"),
]: CATEGORIES[x.key] = x

# Familie, Kind & Baby
for x in [
    _c("fa_kleidung", "Baby- & Kinderkleidung", "https://www.kleinanzeigen.de/s-baby-kinderkleidung/c22", "familie"),
    _c("fa_wagen", "Kinderwagen & Buggys", "https://www.kleinanzeigen.de/s-kinderwagen-buggys/c25", "familie"),
    _c("fa_alten", "Altenpflege", "https://www.kleinanzeigen.de/s-altenpflege/c236", "familie"),
    _c("fa_schuhe", "Baby- & Kinderschuhe", "https://www.kleinanzeigen.de/s-baby-kinderschuhe/c19", "familie"),
    _c("fa_ausstattung", "Baby-Ausstattung", "https://www.kleinanzeigen.de/s-babyausstattung/c258", "familie"),
    _c("fa_sitze", "Babyschalen & Kindersitze", "https://www.kleinanzeigen.de/s-babyschalen-kindersitze/c21", "familie"),
    _c("fa_babysit", "Babysitter/-in & Kinderbetreuung", "https://www.kleinanzeigen.de/s-babysitter-kinderbetreuung/c237", "familie"),
    _c("fa_moebel", "Kinderzimmermöbel", "https://www.kleinanzeigen.de/s-kinderzimmermoebel/c20", "familie"),
    _c("fa_spiel", "Spielzeug", "https://www.kleinanzeigen.de/s-spielzeug/c23", "familie"),
    _c("fa_sonst", "Weiteres Familie, Kind & Baby", "https://www.kleinanzeigen.de/s-familie-kind-baby/sonstiges/c18", "familie"),
]: CATEGORIES[x.key] = x

# Jobs
for x in [
    _c("jo_gastro", "Gastronomie & Tourismus", "https://www.kleinanzeigen.de/s-gastronomie-tourismus/c110", "jobs"),
    _c("jo_bau", "Bau, Handwerk & Produktion", "https://www.kleinanzeigen.de/s-bau-handwerk-produktion/c111", "jobs"),
    _c("jo_mini", "Mini- & Nebenjobs", "https://www.kleinanzeigen.de/s-heimarbeit-mini-nebenjobs/c107", "jobs"),
    _c("jo_ausbild", "Ausbildung", "https://www.kleinanzeigen.de/s-ausbildung/c118", "jobs"),
    _c("jo_buero", "Büroarbeit & Verwaltung", "https://www.kleinanzeigen.de/s-bueroarbeit-verwaltung/c114", "jobs"),
    _c("jo_kunden", "Kundenservice & Call Center", "https://www.kleinanzeigen.de/s-kundenservice-callcenter/c105", "jobs"),
    _c("jo_prakt", "Praktika", "https://www.kleinanzeigen.de/s-praktika/c125", "jobs"),
    _c("jo_sozial", "Sozialer Sektor & Pflege", "https://www.kleinanzeigen.de/s-sozialer-sektor-pflege/c123", "jobs"),
    _c("jo_log", "Transport, Logistik & Verkehr", "https://www.kleinanzeigen.de/s-transport-logistik-verkehr/c247", "jobs"),
    _c("jo_vertrieb", "Vertrieb, Einkauf & Verkauf", "https://www.kleinanzeigen.de/s-vertrieb-einkauf-verkauf/c117", "jobs"),
    _c("jo_sonst", "Weitere Jobs", "https://www.kleinanzeigen.de/s-sonstige-berufe/c109", "jobs"),
]: CATEGORIES[x.key] = x

# Freizeit, Hobby & Nachbarschaft
for x in [
    _c("fr_kunst", "Kunst & Antiquitäten", "https://www.kleinanzeigen.de/s-kunst/c240", "freizeit"),
    _c("fr_sammeln", "Sammeln", "https://www.kleinanzeigen.de/s-sammeln/c234", "freizeit"),
    _c("fr_esoterik", "Esoterik & Spirituelles", "https://www.kleinanzeigen.de/s-esoterik-spirituelles/c232", "freizeit"),
    _c("fr_essen", "Essen & Trinken", "https://www.kleinanzeigen.de/s-essen-trinken/c248", "freizeit"),
    _c("fr_aktiv", "Freizeitaktivitäten", "https://www.kleinanzeigen.de/s-freizeitaktivitaeten/c187", "freizeit"),
    _c("fr_handarbeit", "Handarbeit, Basteln & Kunsthandwerk", "https://www.kleinanzeigen.de/s-handarbeit-basteln-kunsthandwerk/c282", "freizeit"),
    _c("fr_kuenstler", "Künstler/-in & Musiker/-in", "https://www.kleinanzeigen.de/s-kuenstler-musiker/c191", "freizeit"),
    _c("fr_modell", "Modellbau", "https://www.kleinanzeigen.de/s-modellbau/c249", "freizeit"),
    _c("fr_reise", "Reise & Eventservices", "https://www.kleinanzeigen.de/s-reise-eventservices/c233", "freizeit"),
    _c("fr_sport", "Sport & Camping", "https://www.kleinanzeigen.de/s-sport-camping/c230", "freizeit"),
    _c("fr_troedel", "Trödel", "https://www.kleinanzeigen.de/s-troedel-kistenweise/c250", "freizeit"),
    _c("fr_verloren", "Verloren & Gefunden", "https://www.kleinanzeigen.de/s-verloren-gefunden/c189", "freizeit"),
    _c("fr_sonst", "Weiteres Freizeit, Hobby & Nachbarschaft", "https://www.kleinanzeigen.de/s-freizeit-nachbarschaft/sonstiges/c242", "freizeit"),
]: CATEGORIES[x.key] = x

# Musik, Filme & Bücher
for x in [
    _c("mu_buecher", "Bücher & Zeitschriften", "https://www.kleinanzeigen.de/s-buecher-zeitschriften/c76", "musik"),
    _c("mu_filme", "Film & DVD", "https://www.kleinanzeigen.de/s-film-dvd/c79", "musik"),
    _c("mu_buero", "Büro & Schreibwaren", "https://www.kleinanzeigen.de/s-buero-schreibwaren/c281", "musik"),
    _c("mu_comics", "Comics", "https://www.kleinanzeigen.de/s-comics/c284", "musik"),
    _c("mu_fach", "Fachbücher, Schule & Studium", "https://www.kleinanzeigen.de/s-fachbuecher-schule-studium/c77", "musik"),
    _c("mu_cds", "Musik & CDs", "https://www.kleinanzeigen.de/s-musik-cds/c78", "musik"),
    _c("mu_instr", "Musikinstrumente", "https://www.kleinanzeigen.de/s-musikinstrumente/c74", "musik"),
    _c("mu_sonst", "Weitere Musik, Filme & Bücher", "https://www.kleinanzeigen.de/s-musik-film-buecher/sonstiges/c75", "musik"),
]: CATEGORIES[x.key] = x

# Eintrittskarten & Tickets
for x in [
    _c("tk_konzert", "Konzerte", "https://www.kleinanzeigen.de/s-konzerte/c255", "tickets"),
    _c("tk_comedy", "Comedy & Kabarett", "https://www.kleinanzeigen.de/s-comedy-kabarett/c254", "tickets"),
    _c("tk_gutschein", "Gutscheine", "https://www.kleinanzeigen.de/s-gutscheine/c287", "tickets"),
    _c("tk_kinder", "Kinder", "https://www.kleinanzeigen.de/s-kinder/c252", "tickets"),
    _c("tk_sport", "Sport", "https://www.kleinanzeigen.de/s-sport/c257", "tickets"),
    _c("tk_theater", "Theater & Musical", "https://www.kleinanzeigen.de/s-klassik-kultur/c251", "tickets"),
    _c("tk_sonst", "Weitere Eintrittskarten & Tickets", "https://www.kleinanzeigen.de/s-sonstige/c256", "tickets"),
]: CATEGORIES[x.key] = x

# Dienstleistungen
for x in [
    _c("sv_auto", "Auto, Rad & Boot", "https://www.kleinanzeigen.de/s-auto-rad-boot/c289", "services"),
    _c("sv_babysit", "Babysitter/-in & Kinderbetreuung", "https://www.kleinanzeigen.de/s-babysitter-kinderbetreuung/c290", "services"),
    _c("sv_haus", "Haus & Garten", "https://www.kleinanzeigen.de/s-haus-garten/c291", "services"),
    _c("sv_alten", "Altenpflege", "https://www.kleinanzeigen.de/s-altenpflege/c288", "services"),
    _c("sv_elektr", "Elektronik", "https://www.kleinanzeigen.de/s-multimedia-elektronik/c293", "services"),
    _c("sv_kuenstler", "Künstler/-in & Musiker/-in", "https://www.kleinanzeigen.de/s-kuenstler-musiker/c292", "services"),
    _c("sv_reise", "Reise & Event", "https://www.kleinanzeigen.de/s-reise-event/c294", "services"),
    _c("sv_tiere", "Tierbetreuung & Training", "https://www.kleinanzeigen.de/s-tierbetreuung-training/c295", "services"),
    _c("sv_umzug", "Umzug & Transport", "https://www.kleinanzeigen.de/s-umzug-transport/c296", "services"),
    _c("sv_sonst", "Weitere Dienstleistungen", "https://www.kleinanzeigen.de/s-sonstige/c298", "services"),
]: CATEGORIES[x.key] = x

# Verschenken & Tauschen
for x in [
    _c("vg_free", "Verschenken", "https://www.kleinanzeigen.de/s-zu-verschenken/c192", "free"),
    _c("vg_leihen", "Verleihen", "https://www.kleinanzeigen.de/s-verleihen/c274", "free"),
]: CATEGORIES[x.key] = x

# Unterricht & Kurse
for x in [
    _c("ku_nachhilfe", "Nachhilfe", "https://www.kleinanzeigen.de/s-nachhilfe/c268", "kurse"),
    _c("ku_pc", "Computerkurse", "https://www.kleinanzeigen.de/s-computerkurse/c260", "kurse"),
    _c("ku_esoterik", "Esoterik & Spirituelles", "https://www.kleinanzeigen.de/s-esoterik-spirituelles/c265", "kurse"),
    _c("ku_kochen", "Kochen & Backen", "https://www.kleinanzeigen.de/s-kochen-backen/c263", "kurse"),
    _c("ku_kunst", "Kunst & Gestaltung", "https://www.kleinanzeigen.de/s-kunst-gestaltung/c264", "kurse"),
    _c("ku_musik", "Musik & Gesang", "https://www.kleinanzeigen.de/s-musik-gesang/c262", "kurse"),
    _c("ku_sport", "Sportkurse", "https://www.kleinanzeigen.de/s-sportkurse/c261", "kurse"),
    _c("ku_sprache", "Sprachkurse", "https://www.kleinanzeigen.de/s-sprachkurse/c271", "kurse"),
    _c("ku_tanz", "Tanzkurse", "https://www.kleinanzeigen.de/s-tanzkurse/c267", "kurse"),
    _c("ku_weiter", "Weiterbildung", "https://www.kleinanzeigen.de/s-weiterbildung/c266", "kurse"),
    _c("ku_sonst", "Weitere Unterricht & Kurse", "https://www.kleinanzeigen.de/s-sonstige/c270", "kurse"),
]: CATEGORIES[x.key] = x

# Nachbarschaftshilfe
CATEGORIES["hi_hilfe"] = _c("hi_hilfe", "Nachbarschaftshilfe", "https://www.kleinanzeigen.de/s-nachbarschaftshilfe/c401", "hilfe")


def categories_for_group(group_key: str) -> list[Category]:
    return [c for c in CATEGORIES.values() if c.group == group_key]


def group_root_key(group_key: str) -> str:
    return f"g_{group_key}"
