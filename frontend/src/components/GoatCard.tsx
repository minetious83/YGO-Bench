import { useEffect, useState } from 'react';
import { CardArt } from './CardArt';

/**
 * View model for one card, as produced by the backend visibility filter.
 *
 * A hidden card arrives as an opaque stub: the frontend is never given an id it
 * could use to look the card up, so there is nothing here to accidentally
 * reveal. Rendering decisions cannot leak what the payload does not contain.
 */
export type GoatCardView =
  | { visibility: 'hidden'; card_back: true; position?: string }
  | {
      visibility: 'visible';
      card_back: false;
      engine_card_id: number;
      display_image_id: number;
      display_name: string;
      display_text: string;
      variant: 'standard' | 'GOAT' | 'Pre-Errata';
      is_historical: boolean;
      card_types: string[];
      attribute: string | null;
      race: string | null;
      level: number | null;
      attack: number | null;
      defense: number | null;
      image_path: string | null;
      position?: string;
    };

export const isHidden = (card: GoatCardView): card is Extract<GoatCardView, { visibility: 'hidden' }> =>
  card.visibility === 'hidden';

const imageSrc = (card: GoatCardView): string | null =>
  isHidden(card) ? null : card.image_path ?? `/api/cards/${card.display_image_id}/image`;

/** Small badge marking text that is the historical wording our engine runs. */
export function VariantBadge({ variant }: { variant: 'standard' | 'GOAT' | 'Pre-Errata' }) {
  if (variant === 'standard') return null;
  return (
    <span className="goat-variant-badge" title="Historical card text used by the simulator">
      {variant}
    </span>
  );
}

interface ThumbnailProps {
  card: GoatCardView;
  onSelect?: (card: GoatCardView) => void;
  size?: 'sm' | 'md';
}

/**
 * Board/hand/graveyard thumbnail.
 *
 * Selection is driven by tap/click rather than hover, so nothing important is
 * reachable only with a pointer -- mobile is a first-class target.
 */
export function CardThumbnail({ card, onSelect, size = 'md' }: ThumbnailProps) {
  const hidden = isHidden(card);
  const label = hidden ? 'Face-down card' : card.display_name;
  return (
    <button
      type="button"
      className={`goat-card-thumb goat-card-thumb--${size}${hidden ? ' is-hidden' : ''}`}
      onClick={() => onSelect?.(card)}
      aria-label={label}
      disabled={hidden && !onSelect}
    >
      <CardArt src={imageSrc(card)} alt={label} className="goat-card-thumb__art" />
      {!hidden && card.is_historical ? <VariantBadge variant={card.variant} /> : null}
    </button>
  );
}

/** Enlarged preview. Opens on tap; hover is an optional desktop nicety only. */
export function CardPreview({ card, onClose }: { card: GoatCardView; onClose: () => void }) {
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => event.key === 'Escape' && onClose();
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);

  if (isHidden(card)) return null;
  return (
    <div className="goat-card-preview" role="dialog" aria-label={card.display_name} onClick={onClose}>
      <CardArt src={imageSrc(card)} alt={card.display_name} className="goat-card-preview__art" />
    </div>
  );
}

type DetailTab = 'card' | 'rulings';

/**
 * Full detail panel.
 *
 * The Rulings tab is scaffolding only: it will later read a curated, cached
 * research database. It deliberately makes no live request, and it states the
 * authority order rather than implying translated OCG Q&A defines historical
 * GOAT TCG rulings.
 */
export function CardDetailPanel({ card, onClose }: { card: GoatCardView; onClose?: () => void }) {
  const [tab, setTab] = useState<DetailTab>('card');
  if (isHidden(card)) {
    return <aside className="goat-card-detail">This card's identity is not known to you.</aside>;
  }

  const stats = [
    card.card_types.join(' / '),
    card.attribute,
    card.race,
    card.level ? `Level ${card.level}` : null,
    card.attack !== null ? `ATK ${card.attack}` : null,
    card.defense !== null ? `DEF ${card.defense}` : null,
  ].filter(Boolean) as string[];

  return (
    <aside className="goat-card-detail">
      <header className="goat-card-detail__head">
        <h3>{card.display_name}</h3>
        <VariantBadge variant={card.variant} />
        {onClose ? (
          <button type="button" onClick={onClose} aria-label="Close card details">
            ×
          </button>
        ) : null}
      </header>

      <nav className="goat-card-detail__tabs" role="tablist">
        <button type="button" role="tab" aria-selected={tab === 'card'} onClick={() => setTab('card')}>
          Card
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={tab === 'rulings'}
          onClick={() => setTab('rulings')}
        >
          Rulings
        </button>
      </nav>

      {tab === 'card' ? (
        <div className="goat-card-detail__body">
          <CardArt src={imageSrc(card)} alt={card.display_name} className="goat-card-detail__art" />
          <p className="goat-card-detail__stats">{stats.join(' · ')}</p>
          <p className="goat-card-detail__text">{card.display_text}</p>
          {card.is_historical ? (
            <p className="goat-card-detail__note">
              This is the {card.variant} wording the simulator actually runs, not the modern text.
            </p>
          ) : null}
        </div>
      ) : (
        <div className="goat-card-detail__body">
          <p className="goat-card-detail__note">
            Rulings references are not wired up yet. When they are, they will come from a curated
            local cache.
          </p>
          <p className="goat-card-detail__note">
            Authority order: Project Ignis / ocgcore decides what happens in the simulator; our
            engine tests record what it actually does; historical GOAT/TCG sources establish what we
            expect. Translated OCG Q&A is supporting research, and does not by itself define a
            historical GOAT TCG ruling.
          </p>
        </div>
      )}
    </aside>
  );
}
