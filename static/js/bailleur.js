// Fiche d'un bailleur : une société a un nom et un représentant ; une
// personne, une civilité, un prénom et éventuellement un conjoint. Seuls les
// champs du type choisi sont affichés.
(function () {
  var PERSONNE = ["civilite", "prenom", "conjoint_civilite", "conjoint_nom", "conjoint_prenom"];
  var SOCIETE = ["representant_civilite", "representant_nom", "representant_prenom", "representant_qualite", "siren"];

  document.querySelectorAll("main form select[name$='type']").forEach(function (type) {
    var correspondance = /^(.*-)?type$/.exec(type.name);
    var prefixe = (correspondance && correspondance[1]) || "";
    function champ(nom) { return type.form.querySelector("[name='" + prefixe + nom + "']"); }
    if (!champ("representant_nom")) return;
    var libelle = type.form.querySelector("label[for='" + champ("nom").id + "']");
    function majAffichage() {
      var societe = type.value !== "physique";
      PERSONNE.forEach(function (nom) { if (champ(nom)) champ(nom).closest(".champ").hidden = societe; });
      SOCIETE.forEach(function (nom) { if (champ(nom)) champ(nom).closest(".champ").hidden = !societe; });
      if (libelle) libelle.textContent = (societe ? "Raison sociale" : "Nom") + "\u00a0:";
      // Blocs réservés aux sociétés (associés).
      type.form.querySelectorAll("[data-societe]").forEach(function (bloc) { bloc.hidden = !societe; });
    }
    type.addEventListener("change", majAffichage);
    majAffichage();
  });
})();
