// Zone tendue déduite de la commune pendant la saisie d'une fiche de bien.
// L'application répond d'après la liste officielle des communes ; la case
// « Zone tendue » ne s'affiche que si la commune y est introuvable.
(function () {
  function suivre(bloc) {
    var formulaire = bloc.closest("form");
    var codePostal = formulaire && formulaire.querySelector("[name$=code_postal]");
    var ville = formulaire && formulaire.querySelector("[name$=ville]");
    if (!codePostal || !ville) return;
    var deduite = bloc.querySelector("[data-zone-deduite]");
    var inconnue = bloc.querySelector("[data-zone-inconnue]");
    var caseACocher = bloc.querySelector("[data-zone-case]");
    var demande = 0;

    function afficher(reponse) {
      var statut = reponse ? reponse.statut : "sans_liste";
      var connue = statut === "oui" || statut === "non";
      deduite.hidden = !connue;
      inconnue.hidden = statut !== "inconnue";
      caseACocher.hidden = connue;
      if (!connue) return;
      bloc.querySelector("[data-zone-titre]").textContent = "Zone tendue : " + statut;
      bloc.querySelector("[data-zone-detail]").textContent = statut === "oui"
        ? "D'après la commune (" + reponse.commune + "). Préavis d'un mois, honoraires plafonnés à 10 €/m²."
        : "D'après la commune (" + reponse.commune + ").";
    }

    function verifier() {
      if (!/^\d{5}$/.test(codePostal.value.trim()) || !ville.value.trim()) {
        afficher(null);
        return;
      }
      var numero = ++demande;
      var url = bloc.dataset.zoneTendue + "?" + new URLSearchParams({ code_postal: codePostal.value, ville: ville.value });
      fetch(url, { credentials: "same-origin" })
        .then(function (reponse) { return reponse.ok ? reponse.json() : null; })
        .catch(function () { return null; })
        .then(function (reponse) { if (numero === demande) afficher(reponse); });
    }

    codePostal.addEventListener("change", verifier);
    ville.addEventListener("change", verifier);
    verifier();
  }

  document.querySelectorAll("[data-zone-tendue]").forEach(suivre);
})();
