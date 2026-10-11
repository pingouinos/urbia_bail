// Indicatif du pays devant chaque champ téléphone. Le numéro reste un seul
// champ : à l'envoi, un numéro étranger part avec son indicatif (+32…), un
// numéro français tel qu'il est saisi. Sans ce script, le champ accepte
// toujours un numéro écrit avec son indicatif.
(function () {
  var PAYS = [
    ["33", "France"], ["32", "Belgique"], ["41", "Suisse"], ["352", "Luxembourg"], ["49", "Allemagne"],
    ["34", "Espagne"], ["39", "Italie"], ["351", "Portugal"], ["44", "Royaume-Uni"], ["31", "Pays-Bas"],
    ["353", "Irlande"], ["376", "Andorre"], ["377", "Monaco"], ["212", "Maroc"], ["213", "Algérie"],
    ["216", "Tunisie"], ["1", "États-Unis, Canada"], ["590", "Guadeloupe"], ["594", "Guyane"],
    ["596", "Martinique"], ["262", "La Réunion, Mayotte"], ["687", "Nouvelle-Calédonie"], ["689", "Polynésie française"],
  ];
  // Les indicatifs les plus longs d'abord, pour reconnaître +352 avant +35…
  var PAR_LONGUEUR = PAYS.slice().sort(function (a, b) { return b[0].length - a[0].length; });

  document.querySelectorAll("input[type=tel]").forEach(function (champ) {
    if (champ.readOnly || champ.disabled) return;
    var choix = document.createElement("select");
    choix.className = "indicatif";
    choix.setAttribute("aria-label", "Indicatif du pays");
    PAYS.forEach(function (pays) {
      var option = document.createElement("option");
      option.value = pays[0];
      option.textContent = pays[1] + " +" + pays[0];
      choix.appendChild(option);
    });
    var autre = document.createElement("option");
    autre.value = "";
    autre.textContent = "Autre pays";
    choix.appendChild(autre);

    // Un numéro déjà enregistré avec son indicatif se répartit entre les deux.
    var valeur = champ.value.replace(/\s/g, "");
    if (valeur.charAt(0) === "+") {
      var connu = PAR_LONGUEUR.filter(function (pays) { return valeur.indexOf("+" + pays[0]) === 0; })[0];
      if (connu) {
        choix.value = connu[0];
        champ.value = valeur.slice(connu[0].length + 1);
      } else {
        choix.value = "";
      }
    }

    var groupe = document.createElement("div");
    groupe.className = "telephone";
    champ.parentNode.insertBefore(groupe, champ);
    groupe.appendChild(choix);
    groupe.appendChild(champ);

    function indiquer() {
      champ.placeholder = choix.value === "33" ? "06 12 34 56 78" : choix.value ? "Numéro sans l'indicatif" : "+ indicatif et numéro";
    }
    choix.addEventListener("change", indiquer);
    indiquer();

    champ.form.addEventListener("submit", function () {
      var numero = champ.value.trim();
      if (!numero || choix.value === "33" || choix.value === "" || numero.charAt(0) === "+" || numero.indexOf("00") === 0) return;
      // Le zéro initial d'un numéro national ne se compose pas depuis l'étranger.
      champ.value = "+" + choix.value + numero.replace(/[\s.\-()]/g, "").replace(/^0/, "");
    });
  });
})();
