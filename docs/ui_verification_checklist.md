# UI Redesign Verification Checklist

## Route and URL Behavior
- [ ] `GET /` returns `200` and does not redirect.
- [ ] `GET /?race=<slug>&source=<source>[&gender=<gender>]` hydrates matching UI state.
- [ ] Legacy routes `/results/<race>[/<source>[/<gender>]]` redirect to canonical query URLs.

## State Interaction
- [ ] Race changes re-evaluate source availability and gender requirements.
- [ ] Browser Back/Forward restores state via `popstate`.
- [ ] Canonical URL updates on user interaction and is shareable.

## Searchable Race Picker
- [ ] Typing a race label updates selection on `change` / `Enter`.
- [ ] Picker selection updates race state, iframe source, and query URL.

## Visual and Accessibility
- [ ] Theme toggle switches between light/dark and persists across reloads.
- [ ] Core controls are keyboard focusable and have visible labels.
- [ ] Contrast in both themes remains readable for body text and controls.

## Regression
- [ ] Existing slot summary rendering still works after race/source/gender changes.
- [ ] Live results and official iframe loading behavior still works for 70.3 and 140.6 race shapes.
