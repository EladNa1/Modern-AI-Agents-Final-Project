/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  // mupdf ships WASM; keep it external so Next bundles it as a runtime dep, not inlined.
  serverExternalPackages: ["mupdf"],
};

module.exports = nextConfig;
