# chat-nojev

A build of the Jev 聊天助手 conversation copilot in which one ordinary LLM endpoint does both jobs: it reads the conversation and returns the other party's intent, a risk level, whether a reply is due, and the candidate replies, in a single call. You configure one API key instead of two.

Everything else is the upstream app: it reads only what is on your own screen, never hooks or modifies the chat client, and never sends a message for you.

## Status

Work in progress. Nothing to install yet.

## Upstream

Derived from [jev-chat](https://github.com/jev-chat) by Finderchangchang, rezoch340 and the jev-chat contributors, MIT licensed. Their original keeps a separate judgment model (TypeSafe Jev) ahead of the reply model; this variant merges the two calls. See NOTICE for the attribution their licence requires.

## Licence

MIT, inheriting the upstream copyright. See LICENSE.
