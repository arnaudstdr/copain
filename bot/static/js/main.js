// Point d'entrée unique de la PWA (chargé en <script type="module">).
// Step 02 du refacto : tout le code vit encore dans legacy.js, importé tel
// quel ; les steps suivants le découperont en modules dédiés (state, api,
// dashboard, overlays, chat, composer, markdown, ui).
import "./legacy.js";
