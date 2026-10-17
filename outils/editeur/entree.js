// Ce que static/js/editeur.js utilise de TipTap, exposé dans window.Tiptap.
export { Editor, Extension, Node, mergeAttributes } from "@tiptap/core";
export { Plugin } from "@tiptap/pm/state";
export { default as Document } from "@tiptap/extension-document";
export { default as Paragraph } from "@tiptap/extension-paragraph";
export { default as Text } from "@tiptap/extension-text";
export { default as Bold } from "@tiptap/extension-bold";
export { default as Italic } from "@tiptap/extension-italic";
export { default as HardBreak } from "@tiptap/extension-hard-break";
export { UndoRedo } from "@tiptap/extensions";
