# OpenParliament API Gotchas

## Ballots session filter
`/votes/ballots/?session=45-1` filters by the politician's **membership** session,
not the vote's session. In practice these align for current MPs, so the filter is
safe to use. Always also filter client-side by `vote_url` prefix (`/votes/45-1/`)
as a safety net. Without the session param, the API returns all ballots across all
sessions — thousands per active MP — making warmup take hours instead of minutes.

## Politician detail endpoint
Party is returned via `memberships[0].party`, not `current_party` (which is null).

## Pagination
No total count in paginated responses — must follow `next_url` to exhaustion.

## Rate limits
- 60 req/min enforced client-side (sliding window)
- 1s minimum inter-request delay
- 429 backoff uses `Retry-After` header (default 60s)
