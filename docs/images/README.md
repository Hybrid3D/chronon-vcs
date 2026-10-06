# README diagram

`chronon-overview.svg` is the editable source. `chronon-overview.png` is the
2× export (2240 × 920) embedded in the project README for GitHub and PyPI.
Both include an opaque background so their contrast is independent of the
page theme. Keep the two assets in sync when changing the diagram.

To export with [Sharp](https://sharp.pixelplumbing.com/) available in Node.js:

```bash
node -e 'require("sharp")("docs/images/chronon-overview.svg", { density: 144 }).png().toFile("docs/images/chronon-overview.png")'
```

The main README uses an absolute URL to the image in the public
`Hybrid3D/chronon-vcs` repository, so it also works outside GitHub. Publish the
image at that URL before publishing package metadata that refers to it.
