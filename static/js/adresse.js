// Aide à la saisie des adresses, pour les collaborateurs connectés.
// Deux services publics de l'État, sans clé ni compte :
//  - la Base Adresse Nationale (Géoplateforme) propose l'adresse complète ;
//  - l'API Découpage administratif donne la ville d'un code postal.
// C'est une aide : sans réponse du service, les champs se remplissent à la main.
(function () {
  var ADRESSES = "https://data.geopf.fr/geocodage/search";
  var COMMUNES = "https://geo.api.gouv.fr/communes";

  function interroger(url) {
    return fetch(url, { credentials: "omit", referrerPolicy: "no-referrer" })
      .then(function (reponse) { return reponse.ok ? reponse.json() : null; })
      .catch(function () { return null; });
  }

  function remplir(champ, valeur) {
    champ.value = valeur;
    champ.dispatchEvent(new Event("change", { bubbles: true }));
  }

  function suggerer(adresse, codePostal, ville) {
    var liste = document.createElement("ul");
    liste.className = "suggestions";
    liste.hidden = true;
    liste.setAttribute("role", "listbox");
    adresse.parentNode.classList.add("avec-suggestions");
    adresse.insertAdjacentElement("afterend", liste);
    adresse.setAttribute("autocomplete", "off");
    var attente = null, demande = 0, choix = [], actif = -1;

    function fermer() { liste.hidden = true; actif = -1; }
    function marquer() {
      Array.prototype.forEach.call(liste.children, function (ligne, i) {
        ligne.classList.toggle("actif", i === actif);
      });
    }
    function retenir(i) {
      var p = choix[i];
      if (!p) return;
      remplir(adresse, p.name);
      remplir(codePostal, p.postcode);
      remplir(ville, p.city);
      fermer();
    }
    function chercher() {
      var texte = adresse.value.trim();
      if (texte.length < 4) { fermer(); return; }
      var numero = ++demande;
      var url = ADRESSES + "?index=address&autocomplete=1&limit=5&q=" + encodeURIComponent(texte);
      if (/^\d{5}$/.test(codePostal.value.trim())) url += "&postcode=" + codePostal.value.trim();
      interroger(url).then(function (donnees) {
        if (numero !== demande || document.activeElement !== adresse) return;
        choix = ((donnees && donnees.features) || []).map(function (f) { return f.properties; });
        liste.textContent = "";
        choix.forEach(function (p, i) {
          var ligne = document.createElement("li");
          ligne.setAttribute("role", "option");
          ligne.textContent = p.label;
          // mousedown : avant que le champ ne perde le focus.
          ligne.addEventListener("mousedown", function (evenement) { evenement.preventDefault(); retenir(i); });
          liste.appendChild(ligne);
        });
        actif = -1;
        // Juste sous le champ, au-dessus de son texte d'aide.
        liste.style.top = (adresse.offsetTop + adresse.offsetHeight) + "px";
        liste.hidden = !choix.length;
      });
    }
    adresse.addEventListener("input", function () {
      clearTimeout(attente);
      attente = setTimeout(chercher, 250);
    });
    adresse.addEventListener("keydown", function (evenement) {
      if (liste.hidden) return;
      if (evenement.key === "ArrowDown" || evenement.key === "ArrowUp") {
        evenement.preventDefault();
        if (evenement.key === "ArrowDown") actif = (actif + 1) % choix.length;
        else actif = (actif <= 0 ? choix.length : actif) - 1;
        marquer();
      } else if (evenement.key === "Enter" && actif >= 0) {
        evenement.preventDefault();
        retenir(actif);
      } else if (evenement.key === "Escape") {
        fermer();
      }
    });
    adresse.addEventListener("blur", function () { setTimeout(fermer, 150); });
  }

  function villeDuCodePostal(codePostal, ville, numero) {
    var propositions = document.createElement("datalist");
    propositions.id = "villes-" + numero;
    ville.insertAdjacentElement("afterend", propositions);
    ville.setAttribute("list", propositions.id);
    var remplie = "";
    codePostal.addEventListener("input", function () {
      var code = codePostal.value.trim();
      if (!/^\d{5}$/.test(code)) return;
      interroger(COMMUNES + "?fields=nom&codePostal=" + code).then(function (communes) {
        if (!communes || codePostal.value.trim() !== code) return;
        propositions.textContent = "";
        communes.forEach(function (commune) {
          var option = document.createElement("option");
          option.value = commune.nom;
          propositions.appendChild(option);
        });
        // On ne remplace que ce que l'aide a elle-même rempli.
        if (communes.length === 1 && (!ville.value || ville.value === remplie)) {
          remplie = communes[0].nom;
          remplir(ville, remplie);
        } else if (communes.length > 1 && ville.value === remplie) {
          remplie = "";
          remplir(ville, "");
          ville.placeholder = communes.length + " communes : choisir";
        }
      });
    });
  }

  document.querySelectorAll("main form input[name$='adresse']").forEach(function (adresse, numero) {
    var correspondance = /^(.*-)?adresse$/.exec(adresse.name);
    if (!correspondance) return;
    var prefixe = correspondance[1] || "";
    var codePostal = adresse.form.querySelector("[name='" + prefixe + "code_postal']");
    var ville = adresse.form.querySelector("[name='" + prefixe + "ville']");
    if (!codePostal || !ville) return;
    suggerer(adresse, codePostal, ville);
    villeDuCodePostal(codePostal, ville, numero);
  });
})();
