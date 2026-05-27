import type { RGBA } from "@opentui/core"
import type { KeyEvent, Renderable } from "@opentui/core"
import type { Binding } from "@opentui/keymap"
import {
  createBindingLookup as createKeymapBindingLookup,
  type BindingConfig,
  type CreateBindingLookupOptions,
  type KeySequenceFormatPart,
  type SequenceBindingLike,
} from "@opentui/keymap/extras"

export type TuiThemeCurrent = {
  readonly primary: RGBA
  readonly secondary: RGBA
  readonly accent: RGBA
  readonly error: RGBA
  readonly warning: RGBA
  readonly success: RGBA
  readonly info: RGBA
  readonly text: RGBA
  readonly textMuted: RGBA
  readonly selectedListItemText: RGBA
  readonly background: RGBA
  readonly backgroundPanel: RGBA
  readonly backgroundElement: RGBA
  readonly backgroundMenu: RGBA
  readonly border: RGBA
  readonly borderActive: RGBA
  readonly borderSubtle: RGBA
  readonly diffAdded: RGBA
  readonly diffRemoved: RGBA
  readonly diffContext: RGBA
  readonly diffHunkHeader: RGBA
  readonly diffHighlightAdded: RGBA
  readonly diffHighlightRemoved: RGBA
  readonly diffAddedBg: RGBA
  readonly diffRemovedBg: RGBA
  readonly diffContextBg: RGBA
  readonly diffLineNumber: RGBA
  readonly diffAddedLineNumberBg: RGBA
  readonly diffRemovedLineNumberBg: RGBA
  readonly markdownText: RGBA
  readonly markdownHeading: RGBA
  readonly markdownLink: RGBA
  readonly markdownLinkText: RGBA
  readonly markdownCode: RGBA
  readonly markdownBlockQuote: RGBA
  readonly markdownEmph: RGBA
  readonly markdownStrong: RGBA
  readonly markdownHorizontalRule: RGBA
  readonly markdownListItem: RGBA
  readonly markdownListEnumeration: RGBA
  readonly markdownImage: RGBA
  readonly markdownImageText: RGBA
  readonly markdownCodeBlock: RGBA
  readonly syntaxComment: RGBA
  readonly syntaxKeyword: RGBA
  readonly syntaxFunction: RGBA
  readonly syntaxVariable: RGBA
  readonly syntaxString: RGBA
  readonly syntaxNumber: RGBA
  readonly syntaxType: RGBA
  readonly syntaxOperator: RGBA
  readonly syntaxPunctuation: RGBA
  readonly thinkingOpacity: number
}

export type TuiAttentionSoundName = string

export function createBindingLookup(
  config: BindingConfig<Renderable, KeyEvent> | undefined,
  options?: CreateBindingLookupOptions<Renderable, KeyEvent>,
) {
  return createKeymapBindingLookup<Renderable, KeyEvent>(config ?? {}, options)
}

export type TuiKeys = {
  formatSequence: (parts: readonly KeySequenceFormatPart[] | undefined) => string
  formatBindings: (bindings: readonly SequenceBindingLike[] | undefined) => string | undefined
}

export type SlotMode = "single_winner" | "replace" | "multi" | undefined
