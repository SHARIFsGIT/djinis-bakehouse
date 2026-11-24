import { Flame } from "lucide-react";
import { useEffect, useState } from "react";
import { fetchStore } from "../lib/store";
import { DEFAULT_HERO_IMAGE, cacheHeroImage, cachedHeroImage } from "../lib/theme";

/** The page the log-in and sign-up forms sit on — the home page's hero,
 *  with the form where the hero's words are. The form stands on the paper;
 *  from a laptop up, the shop's own photo sits beside it in the hero's
 *  frame, the baker's today-note as its bottom strip. A phone gets the form
 *  alone: the photo is the shop's welcome, the form is the errand. */
export default function AuthLayout({ children }) {
  // The home page's rule: whatever this browser last confirmed, never the
  // shipped default, until the server says which photo is the real one.
  const [photo, setPhoto] = useState(cachedHeroImage);
  const [shown, setShown] = useState(null);
  const [todayNote, setTodayNote] = useState("");

  useEffect(() => {
    fetchStore()
      .then((data) => {
        setPhoto(data.hero_image || DEFAULT_HERO_IMAGE);
        cacheHeroImage(data.hero_image || null);
        setTodayNote(data.today_note || "");
      })
      .catch(() => setPhoto((current) => current ?? DEFAULT_HERO_IMAGE));
  }, []);

  return (
    <div className="mx-auto grid max-w-6xl grid-cols-1 items-center gap-16 px-4 py-10 sm:py-14 lg:grid-cols-[minmax(0,28rem)_minmax(0,1fr)] lg:gap-20 lg:py-16">
      {/* Every field in here stands 48px tall with its words at 16px — the
          shop's tall controls ("Add to basket", the basket's checkout
          button), so a field matches the button under it. Said once, for
          the whole form: the component library reads both from a field's
          surroundings and never sets them on a plain field itself. */}
      <div className="rise-in mx-auto w-full max-w-md lg:mx-0 [--font-size-min:1rem] [--in-size-mul:12]">
        {children}
      </div>

      {/* The photo fades in once it has arrived (the product tiles' own
          manners) over a quiet tile that holds its shape. Lazy, so a phone —
          where the frame is not drawn — never downloads it. */}
      <div className="hidden overflow-hidden rounded-box shadow-xl ring-1 ring-base-300/60 lg:block">
        <div className="photo-tile aspect-6/5">
          {photo && (
            <img
              src={photo}
              alt=""
              loading="lazy"
              decoding="async"
              data-loaded={shown === photo}
              onLoad={() => setShown(photo)}
            />
          )}
        </div>
        {todayNote && (
          <div className="brand-gradient text-primary-content">
            <div className="flex items-center gap-3 px-5 py-3.5">
              <Flame size={20} className="shrink-0 text-accent" aria-hidden="true" />
              <div className="min-w-0">
                <p className="text-[0.65rem] font-bold tracking-[0.22em] uppercase opacity-70">
                  Today
                </p>
                <p className="font-display truncate font-semibold">{todayNote}</p>
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
