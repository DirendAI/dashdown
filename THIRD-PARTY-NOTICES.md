# Third-Party Notices

Dashdown self-hosts (no CDN) a small set of third-party frontend assets, committed
under [`dashdown/static/vendor/`](dashdown/static/vendor/) and shipped in the wheel.
They are vendored as-is by release-only tooling (`tooling/build-assets.mjs`); pip
users never rebuild them. Each retains the license of its upstream project, listed
below. Version pins are in [`tooling/package.json`](tooling/package.json).

| Asset | Project | Version | License |
| --- | --- | --- | --- |
| `vendor/echarts.min.js` | [Apache ECharts](https://echarts.apache.org/) | 5.5.x | Apache-2.0 |
| `vendor/world.json` | World map GeoJSON, bundled with Apache ECharts | 5.5.x | Apache-2.0 |
| `vendor/alpine.min.js` | [Alpine.js](https://alpinejs.dev/) | 3.14.x | MIT |
| `vendor/mermaid.min.js` | [Mermaid](https://mermaid.js.org/) | 11.15.x | MIT |
| `vendor/tailwind.css` (Tailwind portion) | [Tailwind CSS](https://tailwindcss.com/) | 3.4.x | MIT |
| `vendor/tailwind.css` (DaisyUI portion) | [DaisyUI](https://daisyui.com/) | 4.12.x | MIT |
| `vendor/fonts/inter.woff2` | [Inter](https://rsms.me/inter/) (via `@fontsource-variable/inter`) | 5.0.x | SIL OFL 1.1 |

---

## Apache License 2.0 — Apache ECharts (and the bundled `world.json` map)

Copyright The Apache Software Foundation.

ECharts is licensed under the Apache License, Version 2.0. You may obtain a copy of
the License at <https://www.apache.org/licenses/LICENSE-2.0>. The `echarts.min.js`
bundle retains the ASF license banner in its header, and per Section 4(d) of the
License this notice file accompanies the redistribution. The `world.json` map data
is distributed with Apache ECharts under the same terms.

---

## MIT License — Alpine.js, Mermaid, Tailwind CSS, DaisyUI

The following components are distributed under the MIT License:

- Alpine.js — Copyright (c) 2019–present Caleb Porzio and contributors
- Mermaid — Copyright (c) 2014–present Knut Sveidqvist
- Tailwind CSS — Copyright (c) Tailwind Labs, Inc.
- DaisyUI — Copyright (c) Pouya Saadeghi

Permission is hereby granted, free of charge, to any person obtaining a copy of this
software and associated documentation files (the "Software"), to deal in the Software
without restriction, including without limitation the rights to use, copy, modify,
merge, publish, distribute, sublicense, and/or sell copies of the Software, and to
permit persons to whom the Software is furnished to do so, subject to the following
conditions:

The above copyright notice and this permission notice shall be included in all copies
or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED,
INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A
PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT
HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF
CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE
OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.

---

## SIL Open Font License 1.1 — Inter

Copyright (c) 2016–present The Inter Project Authors (<https://github.com/rsms/inter>).

The Inter font (`inter.woff2`) is licensed under the SIL Open Font License, Version 1.1.
The full license text is available at
<https://openfontlicense.org/open-font-license-official-text/>. This Font Software is
distributed "AS IS", without warranty of any kind; see the license for the complete
terms (including the reserved-name and bundling conditions).
