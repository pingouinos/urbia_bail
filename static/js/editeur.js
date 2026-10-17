// Page « Modifier le texte » d'un modèle : éditeur TipTap (static/js/vendor/
// tiptap.js, construit depuis outils/editeur). Les données du modèle sont des
// étiquettes, les blocs « Seulement si » et les tableaux ne se suppriment pas :
// l'application réécrit le Word d'après le document envoyé (documents/editeur.py).
(function () {
  var racine = document.querySelector("[data-editeur]");
  if (!racine || !window.Tiptap) return;
  var T = window.Tiptap;
  var formulaire = racine.closest("form");
  var champ = formulaire.querySelector("[name=texte]");
  var etat = document.querySelector("[data-editeur-etat]");
  var contenu = JSON.parse(document.getElementById("editeur-document").textContent);
  var PROTEGES = ["controle", "tableau"];

  function attribut(nom, rendu) {
    return { default: null, keepOnSplit: false, renderHTML: rendu || function () { return {}; } };
  }

  var Paragraphe = T.Paragraph.extend({
    addAttributes: function () {
      return {
        rang: attribut("rang", function (a) { return a.rang === null ? {} : { id: "rang-" + a.rang }; }),
        titre: attribut("titre", function (a) { return a.titre ? { class: "titre-modele" } : {}; }),
        alerte: attribut("alerte", function (a) { return a.alerte ? { class: "alerte-modele", title: a.alerte } : {}; }),
      };
    },
  });

  var Donnee = T.Node.create({
    name: "donnee", group: "inline", inline: true, atom: true, selectable: true,
    addAttributes: function () { return { code: { default: "" }, libelle: { default: "" } }; },
    parseHTML: function () { return [{ tag: "span[data-code]", getAttrs: function (e) { return { code: e.dataset.code, libelle: e.textContent }; } }]; },
    renderHTML: function (p) { return ["span", { class: "puce-donnee", "data-code": p.node.attrs.code, contenteditable: "false" }, p.node.attrs.libelle || p.node.attrs.code]; },
  });

  function bloc(nom, classe, texte) {
    return T.Node.create({
      name: nom, group: "block", atom: true, selectable: false, draggable: false,
      addAttributes: function () { return { rang: { default: null }, libelle: { default: "" }, apercu: { default: "" } }; },
      parseHTML: function () { return [{ tag: "div[data-" + nom + "]" }]; },
      renderHTML: function (p) {
        var attrs = { class: classe, contenteditable: "false" };
        attrs["data-" + nom] = "";
        return ["div", attrs, texte(p.node.attrs)];
      },
    });
  }

  function compter(doc) {
    var n = 0;
    doc.descendants(function (noeud) { if (PROTEGES.indexOf(noeud.type.name) >= 0) n += 1; });
    return n;
  }

  // Refuse toute modification qui ferait disparaître un bloc protégé.
  var Protection = T.Extension.create({
    name: "protection",
    addProseMirrorPlugins: function () {
      return [new T.Plugin({
        filterTransaction: function (tr, state) { return !tr.docChanged || compter(tr.doc) >= compter(state.doc); },
      })];
    },
  });

  var modifie = false;
  var editeur = new T.Editor({
    element: racine,
    extensions: [
      T.Document, T.Text, T.Bold, T.Italic, T.HardBreak, T.UndoRedo, Paragraphe, Donnee, Protection,
      bloc("controle", "bloc-controle", function (a) { return a.libelle; }),
      bloc("tableau", "bloc-tableau", function (a) { return "Tableau : " + a.apercu; }),
    ],
    content: contenu,
    onUpdate: function () {
      modifie = true;
      if (etat) etat.hidden = false;
    },
  });

  document.querySelectorAll("[data-commande]").forEach(function (bouton) {
    bouton.addEventListener("click", function () {
      var chaine = editeur.chain().focus();
      var commande = bouton.dataset.commande;
      if (commande === "gras") chaine.toggleBold().run();
      else if (commande === "italique") chaine.toggleItalic().run();
      else if (commande === "annuler") chaine.undo().run();
      else if (commande === "retablir") chaine.redo().run();
    });
  });

  var choix = document.querySelector("[data-inserer]");
  if (choix) {
    choix.addEventListener("change", function () {
      var option = choix.selectedOptions[0];
      if (option && option.value) {
        editeur.chain().focus().insertContent({ type: "donnee", attrs: { code: option.value, libelle: option.textContent } }).run();
      }
      choix.value = "";
    });
  }

  formulaire.addEventListener("submit", function () {
    champ.value = JSON.stringify(editeur.getJSON());
    modifie = false;
  });
  window.addEventListener("beforeunload", function (evenement) {
    if (modifie) evenement.preventDefault();
  });
})();
