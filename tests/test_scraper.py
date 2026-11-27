"""Tests del rastreador: formatos de precio, avisos y conservación de datos.

No tocan la red: cada caso construye el HTML o el JSON que devuelve una tienda.
"""
import json
from types import SimpleNamespace

import pytest
import requests

import scraper
from scraper import Oferta, precio_desde_jsonld


# --------------------------------------------------------------------------- #
# Lectura de precios
# --------------------------------------------------------------------------- #


def _jsonld(obj) -> str:
    return f'<html><head><script type="application/ld+json">{json.dumps(obj)}</script></head></html>'


def test_jsonld_prefiere_la_oferta_disponible_a_la_agotada_mas_barata():
    html = _jsonld({
        "@type": "Product", "name": "Steelcase Gesture",
        "offers": [
            {"@type": "Offer", "price": "899", "priceCurrency": "EUR",
             "availability": "https://schema.org/OutOfStock"},
            {"@type": "Offer", "price": "949", "priceCurrency": "EUR",
             "availability": "https://schema.org/InStock"},
        ],
    })
    assert precio_desde_jsonld(html) == ("Steelcase Gesture", 949.0, "EUR", True)


def test_jsonld_si_todo_esta_agotado_devuelve_el_mas_barato_como_agotado():
    html = _jsonld({
        "@type": "Product", "name": "Gesture",
        "offers": [
            {"@type": "Offer", "price": "949", "availability": "https://schema.org/OutOfStock"},
            {"@type": "Offer", "price": "899", "availability": "https://schema.org/OutOfStock"},
        ],
    })
    assert precio_desde_jsonld(html) == ("Gesture", 899.0, "EUR", False)


def test_jsonld_una_oferta_anidada_ilegible_no_tumba_las_demas():
    html = _jsonld({
        "@type": "Product", "name": "Gesture",
        "offers": {"@type": "AggregateOffer", "offers": [
            {"price": "consultar", "priceCurrency": "EUR"},
            {"price": "1099", "priceCurrency": "EUR"},
        ]},
    })
    assert precio_desde_jsonld(html) == ("Gesture", 1099.0, "EUR", True)


def test_jsonld_una_oferta_anidada_en_formato_espanol_se_lee_bien():
    html = _jsonld({
        "@type": "Product", "name": "Gesture",
        "offers": {"@type": "AggregateOffer", "offers": [
            {"price": "1.059,00", "priceCurrency": "EUR"},
            {"price": "1099", "priceCurrency": "EUR"},
        ]},
    })
    assert precio_desde_jsonld(html) == ("Gesture", 1059.0, "EUR", True)


def test_microdatos_con_punto_de_miles_y_sin_decimales():
    html = '<html><title>Silla</title><span itemprop="price" content="1.059"></span></html>'
    assert precio_desde_jsonld(html)[1] == 1059.0


def test_microdatos_con_punto_decimal():
    html = '<html><title>Silla</title><span itemprop="price" content="1059.84"></span></html>'
    assert precio_desde_jsonld(html)[1] == 1059.84


def test_microdatos_formato_espanol():
    html = '<html><title>Silla</title><span itemprop="price" content="1.059,84"></span></html>'
    assert precio_desde_jsonld(html)[1] == 1059.84


def test_open_graph_usa_punto_decimal():
    html = '<html><head><meta property="product:price:amount" content="1059.84"></head></html>'
    assert precio_desde_jsonld(html)[1] == 1059.84


def test_open_graph_formato_espanol():
    html = '<html><head><meta property="product:price:amount" content="1.059,84"></head></html>'
    assert precio_desde_jsonld(html)[1] == 1059.84


# --------------------------------------------------------------------------- #
# Adaptadores
# --------------------------------------------------------------------------- #


class ClienteFalso:
    def __init__(self, respuestas: dict):
        self.respuestas = respuestas
        self.sesion = SimpleNamespace(get=self._get, post=self._post)

    def json(self, url, **kw):
        return self.respuestas[url.split("?")[0] if "search?" not in url else url]

    def _post(self, url, **kw):
        return SimpleNamespace(status_code=200, json=lambda: {"access_token": "t"}, raise_for_status=lambda: None)

    def _get(self, url, **kw):
        return SimpleNamespace(status_code=200, json=lambda: self.respuestas["ebay"], raise_for_status=lambda: None)


def _ebay(monkeypatch, items):
    monkeypatch.setenv("EBAY_CLIENT_ID", "x"); monkeypatch.setenv("EBAY_CLIENT_SECRET", "y")
    cli = ClienteFalso({"ebay": {"itemSummaries": items}})
    return scraper.ebay_api(cli, "eBay", scraper.PRODUCTOS[0])


def test_ebay_sin_coste_de_envio_declarado_deja_el_envio_desconocido(monkeypatch):
    [oferta] = _ebay(monkeypatch, [{
        "title": "Steelcase Gesture", "price": {"value": "500", "currency": "EUR"},
        "itemLocation": {"country": "ES"}, "itemWebUrl": "https://ebay.es/1",
        "shippingOptions": [{"shippingCostType": "CALCULATED"}],
    }])
    assert oferta.envio is None
    assert oferta.a_dict({"EUR": 1.0})["TotalEUR"] is None


def test_ebay_envio_gratis_explicito_si_cuenta_como_cero(monkeypatch):
    [oferta] = _ebay(monkeypatch, [{
        "title": "Steelcase Gesture", "price": {"value": "500", "currency": "EUR"},
        "itemLocation": {"country": "ES"}, "itemWebUrl": "https://ebay.es/1",
        "shippingOptions": [{"shippingCost": {"value": "0.0", "currency": "EUR"}}],
    }])
    assert oferta.envio == 0.0


def test_woocommerce_moneda_sin_decimales():
    cli = ClienteFalso({"https://tienda.test/wp-json/wc/store/v1/products": [{
        "name": "Steelcase Gesture", "permalink": "https://tienda.test/p", "is_in_stock": True,
        "prices": {"price": "1059", "currency_code": "JPY", "currency_minor_unit": 0},
    }]})
    [oferta] = scraper.woocommerce_tienda(cli, "T", scraper.PRODUCTOS[0], "https://tienda.test")
    assert oferta.precio == 1059.0


# --------------------------------------------------------------------------- #
# Cliente HTTP: proxy
# --------------------------------------------------------------------------- #


def test_proxy_url_se_usa_en_los_reintentos(monkeypatch):
    monkeypatch.setenv("PROXY_URL", "http://proxy.test:8080")
    monkeypatch.delenv("SCRAPERAPI_KEY", raising=False)
    monkeypatch.setattr(scraper.time, "sleep", lambda *_: None)
    monkeypatch.setattr(scraper, "guardar_debug", lambda *a, **k: None)
    cli = scraper.Cliente()
    llamadas = []

    def get(url, **kw):
        llamadas.append(kw.get("proxies"))
        return SimpleNamespace(status_code=403 if len(llamadas) == 1 else 200, headers={}, text="")

    cli.sesion.get = get
    cli.get("https://tienda.test/")
    assert llamadas[0] is None
    assert llamadas[1] == {"http": "http://proxy.test:8080", "https": "http://proxy.test:8080"}


# --------------------------------------------------------------------------- #
# Coste y avisos
# --------------------------------------------------------------------------- #


def test_sin_tipo_de_cambio_no_hay_coste_en_euros():
    oferta = Oferta("T", "Gesture", 500.0, "USD", "https://t/1").a_dict({"EUR": 1.0})
    assert oferta["PrecioEUR"] is None
    assert scraper.coste(oferta) is None
    assert scraper.apto_para_aviso(oferta) is False


def _oferta_tygr() -> dict:
    return {"Tienda": "Thomann (DE)", "Producto": "beyerdynamic TYGR 300 R", "PrecioEUR": 158.0,
            "EnvioEUR": 0.0, "TotalEUR": 158.0, "Entrega": "ue",
            "Enlace": "https://www.thomann.es/beyerdynamic_tygr_300_r.htm"}


def test_el_aviso_de_telegram_va_en_html_y_respeta_los_enlaces_con_guiones_bajos():
    titulo, cuerpo = scraper.mensaje_aviso(
        scraper.PRODUCTOS[1], _oferta_tygr(), 158.0, ["por debajo de tu precio objetivo <x> & y"], None)
    texto = scraper.formato_telegram(titulo, cuerpo)
    assert "https://www.thomann.es/beyerdynamic_tygr_300_r.htm" in texto
    assert "&lt;x&gt; &amp; y" in texto
    assert "<b>158,00 €</b>" in texto and "**" not in texto
    assert "<i>No incluye" in texto


def test_telegram_rechazado_se_distingue_de_no_configurado(monkeypatch):
    monkeypatch.setenv("TELEGRAM_TOKEN", "t"); monkeypatch.setenv("TELEGRAM_CHAT_ID", "c")
    cli = SimpleNamespace(sesion=SimpleNamespace(
        post=lambda *a, **k: SimpleNamespace(status_code=400, text="Bad Request: can't parse entities")))
    canales, fallos = scraper.mandar(cli, "t", "c")
    assert canales == [] and len(fallos) == 1 and "400" in fallos[0]
    monkeypatch.delenv("TELEGRAM_TOKEN")
    assert scraper.mandar(cli, "t", "c") == ([], [])


def test_un_fallo_de_red_al_avisar_no_tumba_el_rastreo(monkeypatch):
    monkeypatch.setenv("TELEGRAM_TOKEN", "t"); monkeypatch.setenv("TELEGRAM_CHAT_ID", "c")

    def post(*a, **k):
        raise requests.ConnectionError("sin red")

    canales, fallos = scraper.mandar(SimpleNamespace(sesion=SimpleNamespace(post=post)), "t", "c")
    assert canales == [] and "ConnectionError" in fallos[0]


# --------------------------------------------------------------------------- #
# Conservación de datos antiguos
# --------------------------------------------------------------------------- #


def test_conservar_anteriores_los_marca_como_obsoletos_y_no_los_cuenta_como_frescos():
    anteriores = [{"Articulo": "gesture", "Tienda": "T", "Producto": "G", "Enlace": "u",
                   "Estado": "ok", "PrecioEUR": 500.0, "TotalEUR": 520.0, "Entrega": "es",
                   "Última actualización": "2026-11-01T08:00:00+00:00"}]
    conservadas = scraper.conservar(anteriores)
    assert [d["Estado"] for d in conservadas] == ["obsoleto"]
    assert anteriores[0]["Estado"] == "ok"  # no muta la entrada
    assert scraper.apto_para_aviso(conservadas[0]) is False
