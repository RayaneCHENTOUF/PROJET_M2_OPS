"""Fixtures : base SQLite jetable, dossier de stockage temporaire, faux serveur de flux local."""

from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from collect import db
from collect.config import RACINE, Config

RSS = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel><title>Journal test</title><link>http://exemple.test/</link>
<item><title>Premier article</title><link>http://exemple.test/a1</link><guid>a1</guid>
  <description>Resume un</description><pubDate>Tue, 10 Nov 2026 08:00:00 +0100</pubDate>
  <category>Politique</category></item>
<item><title>Deuxieme article</title><link>http://exemple.test/a2</link>
  <description>Resume deux</description><pubDate>Tue, 10 Nov 2026 09:30:00 GMT</pubDate></item>
<item><title>Doublon interne au flux</title><link>http://exemple.test/a2#comments</link></item>
<item><title>Sans lien</title><guid isPermaLink="false">xyz</guid></item>
</channel></rss>"""

ATOM = """<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom"><title>Atom test</title><id>urn:test</id>
<updated>2026-11-10T10:00:00Z</updated>
<entry><title>Article Atom é</title><id>urn:uuid:1</id>
  <link rel="alternate" href="http://exemple.test/atom1"/>
  <updated>2026-11-10T10:00:00+02:00</updated>
  <summary>Résumé atom</summary><content type="html">TEXTE INTEGRAL</content>
  <category term="Monde"/></entry>
</feed>""".encode("utf-8")


class FauxServeur:
    """Serveur HTTP local : chaque chemin renvoie la reponse qu'on lui programme."""

    def __init__(self):
        self.routes: dict[str, tuple[int, bytes, dict]] = {}
        self.requetes: list[tuple[str, dict]] = []
        serveur = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                serveur.requetes.append((self.path, dict(self.headers)))
                code, corps, en_tetes = serveur.routes.get(self.path, (404, b"introuvable", {}))
                if "ETag" in en_tetes and self.headers.get("If-None-Match") == en_tetes["ETag"]:
                    code, corps = 304, b""
                self.send_response(code)
                for k, v in en_tetes.items():
                    self.send_header(k, v)
                self.send_header("Content-Length", str(len(corps)))
                self.end_headers()
                self.wfile.write(corps)

            def log_message(self, *args):
                pass

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def appels(self, chemin: str) -> int:
        return sum(1 for p, _ in self.requetes if p == chemin)

    def arreter(self):
        self.httpd.shutdown()


@pytest.fixture(autouse=True)
def pas_d_alerte_reelle(monkeypatch):
    """Les tests n'envoient jamais de vrai mail ni de vrai webhook, même si .env est rempli.

    Variables posées à vide : python-dotenv n'écrase pas une variable déjà définie,
    donc les identifiants de .env ne sont pas chargés pendant les tests.
    """
    for nom in ("GAZETTE_SMTP_HOST", "GAZETTE_SMTP_USER", "GAZETTE_SMTP_PASSWORD",
                "GAZETTE_ALERTE_DESTINATAIRES", "GAZETTE_ALERTE_WEBHOOK"):
        monkeypatch.setenv(nom, "")


@pytest.fixture
def serveur():
    s = FauxServeur()
    yield s
    s.arreter()


@pytest.fixture
def cfg(tmp_path: Path) -> Config:
    return Config(
        database_url=f"sqlite:///{(tmp_path / 'test.db').as_posix()}",
        dossier_bruts=tmp_path / "articles_bruts",
        fichier_sources=tmp_path / "sources.csv",
        dossier_migrations=RACINE / "sql" / "migrations",
        timeout_connexion=2, timeout_lecture=2, backoff_base=0.01,
    )


@pytest.fixture
def engine(cfg):
    e = db.moteur(cfg.database_url)
    db.migrer(e, cfg.dossier_migrations)
    yield e
    e.dispose()


def ecrire_sources(cfg: Config, *sources: tuple[str, str]) -> None:
    lignes = ["nom,type,region,acces,url_flux,conditions,frequence,notes"]
    lignes += [f"{nom},PQN,National,RSS,{url},,," for nom, url in sources]
    cfg.fichier_sources.write_text("\n".join(lignes) + "\n", encoding="utf-8")
