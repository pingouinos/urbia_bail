// Page « Modifier le texte » d'un modèle : éditeur TipTap (static/js/vendor/
// tiptap.js, construit depuis outils/editeur). Les données du modèle sont des
// étiquettes ; un passage « Seulement si » se retire par son bouton, un bloc
// « Pour chaque » ne se supprime pas. Le HTML envoyé est nettoyé par le serveur
// (documents/gabarits.py), qui ne garde que ce que l'éditeur sait produire.
(function () {
  var racine = document.querySelector("[data-editeur]");
  if (!racine || !window.Tiptap) return;
  var T = window.Tiptap;
  var formulaire = racine.closest("form");
  var champ = formulaire.querySelector("[name=texte]");
  var etat = document.querySelector("[data-editeur-etat]");
  var contenu = JSON.parse(document.getElementById("editeur-document").textContent);
  var PROTEGES = ["si", "pour"];

  var Paragraphe = T.Paragraph.extend({
    addAttributes: function () {
      return {
        alerte: {
          default: null, keepOnSplit: false,
          parseHTML: function (e) { return e.getAttribute("data-alerte"); },
          renderHTML: function (a) { return a.alerte ? { class: "alerte-modele", title: a.alerte, "data-alerte": a.alerte } : {}; },
        },
      };
    },
  });

  function etiquette(nom, attribut, classe, texte) {
    return T.Node.create({
      name: nom, group: "inline", inline: true, atom: true, selectable: true,
      addAttributes: function () { return { code: { default: "" }, libelle: { default: "" } }; },
      parseHTML: function () {
        return [{ tag: "span[" + attribut + "]", getAttrs: function (e) { return { code: e.getAttribute(attribut), libelle: e.textContent }; } }];
      },
      renderHTML: function (p) {
        var attrs = { class: classe, contenteditable: "false" };
        attrs[attribut] = p.node.attrs.code;
        return ["span", attrs, texte(p.node.attrs)];
      },
    });
  }

  function passage(nom, attribut) {
    return T.Node.create({
      name: nom, group: "block", content: "block+", defining: true,
      addAttributes: function () { return { code: { default: "" } }; },
      parseHTML: function () { return [{ tag: "div[" + attribut + "]", getAttrs: function (e) { return { code: e.getAttribute(attribut) }; } }]; },
      renderHTML: function (p) {
        var attrs = { class: "bloc-condition", "data-libelle": libelleCondition(attribut, p.node.attrs.code) };
        attrs[attribut] = p.node.attrs.code;
        return ["div", attrs, 0];
      },
    });
  }

  function humain(code) {
    return (code || "").split("|")[0].replace(/^not /, "").replace(/_/g, " ").replace(/\./g, " · ");
  }

  function libelleCondition(attribut, code) {
    if (attribut === "data-si") return (/^not /.test(code) ? "Seulement si non : " : "Seulement si : ") + humain(code);
    var parties = code.split(" in ");
    return "Pour chaque " + humain(parties[0]) + " (" + humain(parties[1]) + ")";
  }

  var Tableau = T.Table.extend({
    addAttributes: function () {
      return {
        bordure: {
          default: null,
          parseHTML: function (e) { return e.getAttribute("data-bordure"); },
          renderHTML: function (a) { return a.bordure ? { "data-bordure": a.bordure } : {}; },
        },
      };
    },
  });

  function compter(doc) {
    var n = 0;
    doc.descendants(function (noeud) { if (PROTEGES.indexOf(noeud.type.name) >= 0) n += 1; });
    return n;
  }

  // Refuse une modification qui ferait disparaître un passage conditionnel,
  // sauf par le bouton « Retirer la condition ».
  var Protection = T.Extension.create({
    name: "protection",
    addProseMirrorPlugins: function () {
      return [new T.Plugin({
        filterTransaction: function (tr, state) {
          return !tr.docChanged || tr.getMeta("retrait") || compter(tr.doc) >= compter(state.doc);
        },
      })];
    },
  });

  var modifie = false;
  var editeur = new T.Editor({
    element: racine,
    extensions: [
      T.Document, T.Text, T.Bold, T.Italic, T.Underline, T.HardBreak, T.UndoRedo, Paragraphe,
      T.Heading.configure({ levels: [2, 3] }), T.BulletList, T.OrderedList, T.ListItem, T.HorizontalRule,
      T.TextAlign.configure({ types: ["heading", "paragraph"], alignments: ["left", "center", "right", "justify"], defaultAlignment: "justify" }),
      Tableau.configure({ resizable: false }), T.TableRow, T.TableCell, T.TableHeader,
      etiquette("donnee", "data-donnee", "puce-donnee", function (a) { return a.libelle || humain(a.code); }),
      etiquette("case", "data-case", "puce-donnee puce-case", function (a) { return "☐ " + humain(a.code); }),
      passage("si", "data-si"), passage("pour", "data-pour"), Protection,
    ],
    content: contenu,
    onUpdate: function () {
      modifie = true;
      if (etat) etat.hidden = false;
    },
    onTransaction: actualiser,
  });

  // Passage « Seulement si » qui contient le curseur : (position, nœud) ou null.
  function conditionCourante() {
    var $pos = editeur.state.selection.$from;
    for (var profondeur = $pos.depth; profondeur > 0; profondeur -= 1) {
      if ($pos.node(profondeur).type.name === "si") return { position: $pos.before(profondeur), noeud: $pos.node(profondeur) };
    }
    return null;
  }

  var COMMANDES = {
    titre: function (c) { return c.toggleHeading({ level: 2 }); },
    "sous-titre": function (c) { return c.toggleHeading({ level: 3 }); },
    paragraphe: function (c) { return c.setParagraph(); },
    gras: function (c) { return c.toggleBold(); },
    italique: function (c) { return c.toggleItalic(); },
    souligne: function (c) { return c.toggleUnderline(); },
    puces: function (c) { return c.toggleBulletList(); },
    numeros: function (c) { return c.toggleOrderedList(); },
    gauche: function (c) { return c.setTextAlign("left"); },
    centre: function (c) { return c.setTextAlign("center"); },
    droite: function (c) { return c.setTextAlign("right"); },
    justifie: function (c) { return c.setTextAlign("justify"); },
    saut: function (c) { return c.setHorizontalRule(); },
    annuler: function (c) { return c.undo(); },
    retablir: function (c) { return c.redo(); },
  };
  var ACTIFS = {
    titre: ["heading", { level: 2 }], "sous-titre": ["heading", { level: 3 }], gras: ["bold"], italique: ["italic"],
    souligne: ["underline"], puces: ["bulletList"], numeros: ["orderedList"],
    gauche: [{ textAlign: "left" }], centre: [{ textAlign: "center" }], droite: [{ textAlign: "right" }], justifie: [{ textAlign: "justify" }],
  };

  document.querySelectorAll("[data-commande]").forEach(function (bouton) {
    bouton.addEventListener("click", function () {
      var commande = bouton.dataset.commande;
      if (commande === "retirer-condition") {
        var condition = conditionCourante();
        if (!condition) return;
        var tr = editeur.state.tr.replaceWith(condition.position, condition.position + condition.noeud.nodeSize, condition.noeud.content);
        editeur.view.dispatch(tr.setMeta("retrait", true));
        editeur.commands.focus();
        return;
      }
      COMMANDES[commande](editeur.chain().focus()).run();
    });
  });

  function actualiser() {
    if (!editeur) return;
    document.querySelectorAll("[data-commande]").forEach(function (bouton) {
      var actif = ACTIFS[bouton.dataset.commande];
      if (actif) bouton.setAttribute("aria-pressed", editeur.isActive.apply(editeur, actif) ? "true" : "false");
      if (bouton.dataset.commande === "retirer-condition") bouton.disabled = !conditionCourante();
    });
  }

  var donnee = document.querySelector("[data-inserer]");
  if (donnee) {
    donnee.addEventListener("change", function () {
      var option = donnee.selectedOptions[0];
      if (option && option.value) {
        editeur.chain().focus().insertContent({ type: "donnee", attrs: { code: option.value, libelle: option.textContent } }).run();
      }
      donnee.value = "";
    });
  }
  var condition = document.querySelector("[data-condition]");
  if (condition) {
    condition.addEventListener("change", function () {
      if (condition.value) editeur.chain().focus().wrapIn("si", { code: condition.value }).run();
      condition.value = "";
    });
  }

  document.querySelectorAll("[data-titre]").forEach(function (lien) {
    lien.addEventListener("click", function (evenement) {
      evenement.preventDefault();
      var titre = racine.querySelectorAll("h2")[Number(lien.dataset.titre)];
      if (titre) titre.scrollIntoView({ behavior: "smooth", block: "start" });
    });
  });

  actualiser();
  formulaire.addEventListener("submit", function () {
    champ.value = editeur.getHTML();
    modifie = false;
  });
  window.addEventListener("beforeunload", function (evenement) {
    if (modifie) evenement.preventDefault();
  });
})();
