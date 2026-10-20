# Anwendbarkeit des Cyber Resilience Act (öffentliche Fassung)

> Erzeugt aus dem internen Register `docs/compliance/anwendbarkeit.toml` mit `scanview-nachweise register darstellen` — nicht von Hand bearbeiten.

- **Entschieden am:** 2026-09-27
- **Status:** ungeklärt — handeln, als gälte Art. 14

Ob und in welcher Rolle die Pflichten der Verordnung (EU) 2024/2847 (Cyber Resilience Act) für scanview gelten, hängt vom Betriebs- und Vertriebsmodell ab und ist noch nicht abschließend rechtlich geklärt. Bis dahin wird gehandelt, als gälten die Herstellerpflichten einschließlich der Meldepflichten nach Art. 14: Meldeweg, Schwachstellenbehandlung, Sicherheitsupdates und technische Dokumentation sind Voraussetzung jedes Releases (siehe `docs/public/kontrollmatrix.md`).

## Betriebs- und Vertriebsmodelle

| Modell | Produktgrenze | Bereitstellung seit | Einordnung |
|---|---|---|---|
| Gehostete Plattform (Hosting, Browser-Viewer) | Server + ausgeliefertes Web-Bundle | 27.09.2026 | ungeklärt (SaaS-Ausnahme Erw. 12 vs. Web-Bundle als Produkt) |
| Open-Source-Veröffentlichung | Export-Repo + Release-Artefakte | noch nicht | ungeklärt (Erw. 15/18, Art. 24 Steward) |
| Installation beim Kunden | Server, Import-Werkzeug, Container | noch nicht | Hersteller, Art. 14 ab Bereitstellung |
