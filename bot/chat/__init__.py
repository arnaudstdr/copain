"""Historique d'affichage des bulles du mode dialogue (table `chat_messages`).

Distinct de l'history roulante en mémoire (`BotDeps.history`, contexte LLM) et
de la mémoire sémantique ChromaDB : ce module persiste les échanges du mode
dialogue de la PWA pour pouvoir réafficher les bulles passées, datées, après
un reload ou un redémarrage du serveur (canal d'affichage, non critique).
"""
