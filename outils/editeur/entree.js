// Ce que static/js/editeur.js utilise de TipTap, exposé dans window.Tiptap.
export { Editor, Extension, Node, mergeAttributes } from "@tiptap/core";
export { Plugin } from "@tiptap/pm/state";
export { default as Document } from "@tiptap/extension-document";
export { default as Paragraph } from "@tiptap/extension-paragraph";
export { default as Text } from "@tiptap/extension-text";
export { default as Bold } from "@tiptap/extension-bold";
export { default as Italic } from "@tiptap/extension-italic";
export { default as Underline } from "@tiptap/extension-underline";
export { default as HardBreak } from "@tiptap/extension-hard-break";
export { default as Heading } from "@tiptap/extension-heading";
export { default as HorizontalRule } from "@tiptap/extension-horizontal-rule";
export { default as TextAlign } from "@tiptap/extension-text-align";
export { BulletList, OrderedList, ListItem } from "@tiptap/extension-list";
export { Table, TableRow, TableCell, TableHeader } from "@tiptap/extension-table";
export { UndoRedo } from "@tiptap/extensions";
