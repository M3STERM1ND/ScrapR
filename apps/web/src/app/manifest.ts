import type { MetadataRoute } from "next";

/**
 * What an installed ScrapR is called and drawn with on a home screen.
 *
 * Both icons are the file-convention images beside the root layout, so there
 * is one source for the app icon rather than a second copy in `public/`.
 */
export default function manifest(): MetadataRoute.Manifest {
  return {
    name: "ScrapR",
    short_name: "ScrapR",
    description: "Research that shows its sources.",
    start_url: "/",
    display: "browser",
    background_color: "#f5f3f0",
    theme_color: "#f5f3f0",
    icons: [
      { src: "/icon.png", sizes: "512x512", type: "image/png", purpose: "any" },
      { src: "/apple-icon.png", sizes: "180x180", type: "image/png" },
    ],
  };
}
