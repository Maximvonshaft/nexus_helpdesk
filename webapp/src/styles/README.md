# styles layer

Material UI and `src/theme/nexusTheme.ts` are the sole visual authorities.

Only two bounded source stylesheets are permitted:

- `src/styles.css` for browser-level foundations;
- `src/a11y.css` for the screen-reader-only utility.

Custom token files, shared component CSS, route CSS and feature palettes are prohibited. Feature layout and responsive behavior use MUI components, theme values and `sx`/responsive props.
