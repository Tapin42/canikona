# UI State Touchpoints

This document maps the primary files that participate in the home-page rendering and state flow.

- `app.py`: canonical state normalization, route compatibility, and default race/source/gender selection.
- `templates/index.html`: control surface (race, data source, gender), slot summary mount, and results iframe container.
- `static/js/home.js`: client state transitions, canonical query URL sync, popstate handling, and searchable race input wiring.
- `templates/partials/nav.html`: Home navigation and theme toggle mount.
- `static/js/nav.js`: dark-mode persistence and nav menu behavior.
- `static/css/styles.css`: tokenized theme palette, component surfaces, and responsive layout styles.
