// ── Dictée vocale (Web Speech API) ──────────────────────────────────────────
// Portage du bloc micro de bot/static/js/composer.js (_toggleMic). Reconnaissance
// vocale fr-FR, un seul enregistrement à la fois par instance de hook. L'API
// n'étant pas dans les libs TS standard, on en déclare localement le minimum
// utilisé. Support Safari iOS variable → `supported` permet un fallback propre
// (le composant affiche un toast si non supporté), on ne plante jamais.

import { useCallback, useEffect, useRef, useState } from "react";

// Types minimaux de la Web Speech API (non fournis par TypeScript).
interface SpeechRecognitionLike {
  lang: string;
  continuous: boolean;
  interimResults: boolean;
  onresult: ((e: SpeechRecognitionEventLike) => void) | null;
  onerror: (() => void) | null;
  onend: (() => void) | null;
  start: () => void;
  stop: () => void;
}
interface SpeechRecognitionEventLike {
  results: ArrayLike<ArrayLike<{ transcript: string }>>;
}
type SpeechRecognitionCtor = new () => SpeechRecognitionLike;

function getCtor(): SpeechRecognitionCtor | null {
  const w = window as unknown as {
    SpeechRecognition?: SpeechRecognitionCtor;
    webkitSpeechRecognition?: SpeechRecognitionCtor;
  };
  return w.SpeechRecognition ?? w.webkitSpeechRecognition ?? null;
}

interface Options {
  /** Transcription obtenue (à concaténer au champ de saisie). */
  onResult: (transcript: string) => void;
  /** Erreur de reconnaissance (le composant affiche un toast). */
  onError?: () => void;
}

/**
 * `supported` = l'API est disponible dans ce navigateur.
 * `recording` = un enregistrement est en cours (pilote la classe `.recording`).
 * `toggle()` démarre/arrête ; renvoie `false` si l'API n'est pas supportée
 * (le composant peut alors afficher un toast « micro non supporté »).
 */
export function useSpeechRecognition({ onResult, onError }: Options) {
  const [recording, setRecording] = useState(false);
  const recognitionRef = useRef<SpeechRecognitionLike | null>(null);
  const supported = useRef(getCtor() !== null).current;
  // Callbacks lus via ref : le toggle reste stable sans recréer l'instance.
  const onResultRef = useRef(onResult);
  onResultRef.current = onResult;
  const onErrorRef = useRef(onError);
  onErrorRef.current = onError;

  const toggle = useCallback((): boolean => {
    const Ctor = getCtor();
    if (!Ctor) return false;
    if (recognitionRef.current) {
      recognitionRef.current.stop();
      recognitionRef.current = null;
      setRecording(false);
      return true;
    }
    const r = new Ctor();
    r.lang = "fr-FR";
    r.continuous = false;
    r.interimResults = false;
    r.onresult = (e) => {
      onResultRef.current(e.results[0][0].transcript);
    };
    r.onerror = () => {
      onErrorRef.current?.();
    };
    r.onend = () => {
      recognitionRef.current = null;
      setRecording(false);
    };
    recognitionRef.current = r;
    setRecording(true);
    r.start();
    return true;
  }, []);

  // Coupe l'enregistrement si le composant se démonte pendant l'écoute.
  useEffect(
    () => () => {
      recognitionRef.current?.stop();
      recognitionRef.current = null;
    },
    [],
  );

  return { supported, recording, toggle };
}
