# Capacity-constrained sequential offer acceptance (tender offers vs. net/gross limits)

Scope: turn the dynamic and stochastic knapsack, revenue-management bid-price, reusable-resource and inventory-constrained market-making literature into acceptance rules for the RITC liability/tender bot. Case parameters, as briefed: 420 s heat, about 11 tenders (one every 30-35 s), 50-59k shares each, BUY or SELL in 3 stocks, 30 s decision window, net limit 100k (sum of signed positions), gross limit 250k, unwind 2,500-3,600 sh/s (slower when liquidity dries up), and free close at the last price. Notation used throughout: L = net limit (100k), q = block size (about 55k), n = signed net position, u = unwind rate (shares/s), lambda = offer arrival rate (about 1/33 per s), W = how long a blocked tender can wait for room (up to about 28 s with `room_wait`), F = seconds after a fill before unwinding can start (booking delay, or a front-running freeze while a tender in that stock is pending), S = expected surplus of the next offer = E[max(edge*q - cost, 0)].

Where a number comes from my own toy model and not from a source, it is labelled "toy DP (my computation)". The toy DP script is `dp3.py` in the session scratchpad. It is a pure-python backward induction over net position n in 1k-share steps, with 1 s steps, offers arriving with probability 1/33 per second, direction ±1 with equal odds, q = 55k, and edge ~ U(-0.87, +0.78) $/share.

---

## Q1. Dynamic and stochastic knapsack (DSKP) and RM bid-price controls: do closed-form or simple thresholds exist?

### Takeaway
Yes. The optimal policy is a threshold rule: accept an offer if and only if its reward is at least the drop in expected future value caused by the capacity it uses (the "bid price" or opportunity cost), V_{t-1}(x) - V_{t-1}(x - A_j). With only one resource dimension (net room) and near-equal block sizes, this function is small enough to compute exactly by DP, so the bot does not need the asymptotic or LP approximations. Those approximations are justified only when there are many arrivals and capacity is large, and neither holds with about 11 lumpy blocks.

### Cited Findings
- DSKP definition: items arrive by a Poisson process, each with a size (demand for a limited resource) and a reward. Accepting earns the reward, rejecting may incur a penalty, and the objective is maximum expected value. For equal sizes and random rewards, Kleywegt & Papastavrou (Operations Research 1998) derive optimal solutions and recursive algorithms for computing them, with closed forms in special cases. — [IDEAS: Kleywegt & Papastavrou 1998](https://ideas.repec.org/a/inm/oropre/v46y1998i1p17-35.html)
- DSKP lineage: discrete-time analysis by Papastavrou, Rajagopalan & Kleywegt (1996), continuous time by Kleywegt & Papastavrou (1998), and random-sized items in Kleywegt & Papastavrou (2001). — [Jung et al. 2024, arXiv:2402.14269](https://arxiv.org/pdf/2402.14269)
- With complete information, Papastavrou-Rajagopalan-Kleywegt and Kleywegt-Papastavrou "characterized optimal policies in terms of thresholds". Gallego & van Ryzin show revenue is concave in capacity when all requests have equal weight. Kleywegt & Papastavrou give examples where value is not concave in capacity when weights differ. — [Dizdar, Gershkov & Moldovanu 2009 (pdf)](https://pluto.huji.ac.il/~alexg/pdf/knapsack.pdf)
- The per-unit acceptance threshold for a request of weight w at capacity c is set by the Bellman equation with the opportunity cost R(c, T-t) - R(c-w, T-t) on the right-hand side (Theorem 1, eq. 1: w*[p - (1-F(p|w))/f(p|w)] = R(c,T-t) - R(c-w,T-t), where the left side is the "virtual value" form used when values are private). With values known, as in our case, the rule reduces to "accept if w*v >= R(c,T-t) - R(c-w,T-t)". — [Dizdar, Gershkov & Moldovanu 2009](https://pluto.huji.ac.il/~alexg/pdf/knapsack.pdf)
- A time-independent, weight-dependent price policy derived from a deterministic (fluid) problem is asymptotically optimal as capacity and time go to infinity. With independent w and v, its revenue ratio is at least 1 - sqrt(E[w^2]/E[w]) / (2*sqrt(min(C, E[w]*T))) (Theorem 5). — [Dizdar, Gershkov & Moldovanu 2009](https://pluto.huji.ac.il/~alexg/pdf/knapsack.pdf)
- Lin, Lu & Yao: policies that "start by accepting only high value requests, and then switch-over to accepting also lower values as time goes by" are asymptotically optimal with well-chosen switch-over times. — [Dizdar, Gershkov & Moldovanu 2009, Remark 2](https://pluto.huji.ac.il/~alexg/pdf/knapsack.pdf)
- Talluri & van Ryzin (Management Science 1998), Proposition 1: the optimal control is u*_k^j(x, r) = 1 if r^j >= J_{k-1}(x) - J_{k-1}(x - A^j) and A^j <= x, else 0. This means "we accept a fare ... only when it exceeds the opportunity cost of the reduction in leg capacities." — [Talluri & van Ryzin 1998 (pdf)](https://business.columbia.edu/sites/default/files-efs/pubfiles/3933/network_revenue_management.pdf)
- An additive (per-unit) bid-price control is not optimal in general. Their two-period counter-example (one seat per leg, local fares $250 vs. a through fare $500) shows a single per-unit price can make the wrong accept/deny decision. Bid prices are asymptotically optimal only "when leg capacities and sales volumes are large". — [Talluri & van Ryzin 1998](https://business.columbia.edu/sites/default/files-efs/pubfiles/3933/network_revenue_management.pdf)
- Talluri & van Ryzin note that "ad-hoc group bookings ... one needs an accurate assessment of opportunity cost to make a good decision". They also note that the deterministic-LP bid price "considers only the mean demand" and gives zero duals on any leg whose mean demand is below capacity. Re-solving frequently helps. — [Talluri & van Ryzin 1998](https://business.columbia.edu/sites/default/files-efs/pubfiles/3933/network_revenue_management.pdf)
- Bid-price approximations are gradients of an approximate value function: J_k(x) - J_k(x - A^j) ≈ grad J^A_k(x) · A^j. — [Talluri & van Ryzin 1998](https://business.columbia.edu/sites/default/files-efs/pubfiles/3933/network_revenue_management.pdf)
- Prophet and secretary benchmarks: with known distributions, backward induction is optimal. Set VAL_{n:n} = E[v_n] and VAL_{i:n} = E[max(v_i, VAL_{i+1:n})], then accept v_i if v_i >= VAL_{i+1:n}. Samuel-Cahn (1984): a single threshold (median of max v, or E[max v]/2) guarantees at least 1/2 of E[max v]. — [ADFOCS-24 prophet-inequality lecture notes](https://conferences.mpi-inf.mpg.de/adfocs-24/material/Paul/pt1.pdf)
- Vera & Banerjee's "Fluid Bayes Selector" re-solves the fluid LP max r'x s.t. Ax <= B_t, x <= E[Z(t)] at every arrival. It accepts type j if and only if X_jt >= E[Z_j(t)]/2 and the item is feasible. For the multi-secretary problem its expected regret is at most r_max * sum_{j>1} 2/p_j, which is independent of the horizon T and the budget B. — [Vera & Banerjee, arXiv:1901.05028](https://arxiv.org/abs/1901.05028)

### Inferences
- Map to the case. The resource is the net room L - |n|, and each tender is an item of weight about q (the sign depends on direction; see Q4). The exact rule is: accept if edge*q - unwind_cost >= BP(t, n, d), where BP = V_t(n_cont) - V_t(n + d*q). The current rule ("accept if modelled profit/share >= $0.01 and the block fits") uses a constant BP of $0.01*q, which ignores the occupancy state.
- Block sizes are near-equal (50-59k), which is the equal-weight case where Gallego-van Ryzin concavity holds. In that case BP rises as room falls, which is the "threshold rises with occupied capacity" shape the brief asks for. Because each block is 50-59% of L, the per-share LP/fluid bid price is unreliable (Talluri & van Ryzin's lumpy group-request warning). Use the exact DP or the closed-form in Q2 instead.
- Toy DP (my computation), u = 3k/s, F = 0, W = 0. BP for a BUY block is $0.010/share at n = 0, $0.027 at n = +20k and $0.049 at n = +45k. At u = 1k/s these become $0.028, $0.073 and $0.116. BP is nearly flat in time until the last ~30-40 s and then falls: at u = 1k/s, n = +45k, t = 390 s it is $0.073, against $0.116 at t = 0.
- Comparing these numbers with the case's edges: fixed tenders that get accepted average about +$0.55/share, far above any of these BPs, so BP almost never flips a fixed-tender decision. Auctions near reserve (~$0.10/share) sit in the same range as BP, so occupancy-dependent pricing matters mainly for auctions and for marginal fixed tenders. That fits the observed loss from winning low-edge auctions.
- Robustness with about 11 arrivals: the asymptotic guarantees (Dizdar Theorem 5, Talluri-van Ryzin asymptotics) do not apply. The DP is exact under its assumptions, and Vera-Banerjee's re-solving has regret bounds independent of T. Both favour recomputing BP every second from current state rather than fixing a static threshold.

### Gaps
- I could not open the Kleywegt & Papastavrou 1998/2001 full texts (the INFORMS supplement returned 403). I therefore have no verbatim threshold or stopping-rule formula from them. The threshold structure is cited from Dizdar et al.'s summary of their work.
- I found no paper on a DSKP where items have signed weights on a two-sided capacity (|n| <= L, with opposite items freeing capacity). The direction results in Q4 come from my own model.

---

## Q2. Capacity that regenerates (inventory being unwound): reusable-resource and queueing results, and how they change the threshold

### Takeaway
When capacity comes back over time, the shadow price is local in time. It is roughly the probability that a valuable same-direction offer arrives while the room is still occupied, times that offer's surplus. A closed-form approximation tracks the toy DP to within about $0.005/share:

BP(n_after) ≈ S * (1 - exp(-lambda_same * max(0, tau_free - W)))

where tau_free = F + max(0, |n_after| - (L - q)) / u is the time until a further same-direction block fits again. Fast unwinding (u ≈ 3k/s) plus waiting for room makes BP close to zero. Freezes (F), slow liquidity (u ≈ 1k/s) and pending auction reservations are what make it material.

### Cited Findings
- Levi & Radovanovic model revenue management with reusable resources as loss networks. A simple LP gives an upper bound on the long-run revenue rate, and the LP's "class selection policy" achieves at least 50% of optimal for a single resource with uniform requirements. It is asymptotically optimal as capacity grows relative to the largest requirement. — [Levi & Radovanovic, MIT DSpace](https://dspace.mit.edu/handle/1721.1/65428?show=full)
- Admission control to multi-class Markovian finite-capacity queues has an optimal "(randomized) trunk reservation" policy. Acceptance thresholds for customer types are ordered by reward, so a class is admitted only while occupancy is below its own threshold. — [Optimality of randomized trunk reservation (Adv. Appl. Prob.)](https://www.cambridge.org/core/journals/advances-in-applied-probability/article/optimality-of-randomized-trunk-reservation-for-a-problem-with-a-single-constraint/2EF129A770B26F73B26D611DDF7B5D22)
- Miller (Management Science 1969) set up the founding model: an n-server system with m classes that differ by reward, formulated as an infinite-horizon continuous-time MDP with Poisson arrivals and exponential service. — [IDEAS: Miller 1969](https://ideas.repec.org/a/inm/ormnsc/v16y1969i3p234-245.html)
- Online reusable allocation. The "Balance" rule allocates to argmax r_i * (1 - g(y_i/c_i)), where y_i/c_i is the remaining fraction; g(x) = e^{-x} is (1 - 1/e)-competitive for non-reusable resources. With reusable resources and random durations, Balance falls below 0.626. The RBA fix uses the rank of the highest available unit z_i(t) >= y_i(t), so "units that are in use but return soon" count as effective inventory. — [Goyal, Iyengar & Udwani, arXiv:2002.02430](https://arxiv.org/pdf/2002.02430)
- Feng et al. (2021, cited there) show that Balance stays (1 - 1/e)-competitive when usage durations are deterministic. — [Goyal, Iyengar & Udwani, arXiv:2002.02430](https://arxiv.org/pdf/2002.02430)

### Inferences
- An unwinding position works like a reusable resource whose "service time" is under our control. It is set by u, the freeze F and liquidity. Goyal et al.'s point that soon-to-return capacity should be counted as available matches what `room_wait` already does: room that frees within the 30 s window is effectively still available.
- Derivation of the closed form (my own). A same-direction offer arrives at rate lambda_same ≈ lambda/2. It is blocked, and not rescued by waiting, only if it arrives in the first max(0, tau_free - W) seconds. If it is blocked, we lose its surplus S. With uniform edges on [-0.87, 0.78], S = 55k * 0.78^2 / (2 * 1.65) ≈ $10.1k.
- Validation against the toy DP (my computation), q = 55k, lambda = 1/33:

| u (k/s) | F (s) | W (s) | n before | closed-form BP ($/sh) | DP BP ($/sh) |
|---|---|---|---|---|---|
| 3 | 0 | 0 | 0 | 0.009 | 0.010 |
| 1 | 0 | 0 | 0 | 0.026 | 0.028 |
| 1 | 0 | 0 | +45k | 0.104 | 0.116 |
| 3 | 15 | 0 | 0 | 0.045 | 0.040 |
| 3 | 0 | 28 | 0 | 0.000 | 0.001 |
| 1 | 15 | 28 | +45k | (tau = 70, so 42 s exposed) 0.086 | 0.111 |

  The DP is higher where repeated blocking compounds. As a rule of thumb, the closed form understates BP by 10-25% when |n_after| is near L.
- Toy DP values (my computation): room-wait (W = 28 s) raises expected heat value by about $3-8k across these settings (u = 3, F = 0: $138.4k to $141.5k; u = 1, F = 15: $125.7k to $133.8k). A 15 s freeze after each fill costs about $6-10k without room-wait. This is consistent with the repo's measured gain from `room_wait` (+$3-6k/heat in config/liability.toml comments).
- Time-remaining factor. Replace tau_free - W by min(tau_free - W, T_left_for_offers), where T_left_for_offers is the time until the last tender that can still be accepted. BP then falls to zero near the end. The free close-out at the last price also means a late block never needs unwinding, so near the bell the only remaining cost of room is the next 0-1 offers.

### Gaps
- I did not find a paper that treats the "service time" (unwind speed) as a decision paid for in execution cost. The closest material is the block-pricing work in Q3/Q5.
- I could not access Miller (1969) or Key's trunk-reservation results directly. The ordering-by-reward statement is cited from the Advances in Applied Probability abstract.

---

## Q3. Dealer and market-making models where inventory limits create a shadow price on accepting flow

### Takeaway
In Avellaneda-Stoikov, the inventory shadow price is linear in position and in remaining risk time: r = s - q*gamma*sigma^2*(T - t). Guéant-Lehalle-Fernandez-Tapia add a hard bound Q, so quoting on the side that breaches it stops, and quotes skew linearly in q. Block-pricing theory (Guéant) says the risk-liquidity premium is convex (superlinear) in block size and non-increasing in the time allowed to liquidate. These models price risk and impact. The knapsack bid price prices forgone future offers. The bot should add both.

### Cited Findings
- Avellaneda-Stoikov: the reservation (indifference) price is r(s, q, t) = s - q*gamma*sigma^2*(T - t) (eq. 8), with reservation ask/bid r^a/r^b = s + (1 -/+ 2q)*gamma*sigma^2*(T - t)/2 (eqs. 6-7). When long (q > 0) the reservation price is below mid ("a desire to liquidate"), and the agent is "willing to buy at a higher price" when short. — [Avellaneda & Stoikov 2008 (pdf)](https://www.math.nyu.edu/~avellane/HighFrequencyTrading.pdf)
- Guéant, Lehalle & Fernandez-Tapia add an inventory bound Q: a market maker at +Q "will never set a bid quote" (and symmetrically at -Q), described as "a risk limit". The HJB reduces to linear ODEs, with v_q'(t) = alpha*q^2*v_q - eta*(v_{q-1} + v_{q+1}), alpha = (k/2)*gamma*sigma^2. Optimal bid distance is delta^b(t, q) = (1/k)*ln(v_q/v_{q+1}) + (1/gamma)*ln(1 + gamma/k). — [Guéant, Lehalle & Fernandez-Tapia, arXiv:1105.3115](https://arxiv.org/pdf/1105.3115v5)
- Closed-form asymptotic approximation: delta^b(q) ≈ (1/gamma)*ln(1 + gamma/k) + ((2q+1)/2)*sqrt(sigma^2*gamma/(2kA) * (1 + gamma/k)^(1 + k/gamma)), and delta^a(q) is the same with -(2q-1). The skew is therefore linear in inventory q. — [Guéant, Lehalle & Fernandez-Tapia, arXiv:1105.3115](https://arxiv.org/pdf/1105.3115v5)
- Optimal quotes depend on the distance to the reference price, the current inventory and the time remaining. — [arXiv:1105.3115 abstract](https://arxiv.org/abs/1105.3115v5)
- Guéant block pricing: P(T, q0, S0) = q0*S0 - ∫_0^{q0} F(z) dz - psi*q0 - I_0(q*), which is mark-to-market minus permanent impact, proportional cost and an optimized risk/impact term. The risk-liquidity premium is "nonincreasing" in the time allowed (Corollary 3.1), nondecreasing in size (Prop. 3.2) and "convex (and in practice superlinear)" in block size (Prop. 3.3). With execution cost L(rho) = eta*|rho|^(1+phi) and constant volume V, the premium term scales as q^((1+3phi)/(1+phi)) (Prop. 3.8). — [Guéant, arXiv:1210.6372](https://arxiv.org/pdf/1210.6372)

### Inferences
- In the tender setting, the relevant risk horizon is the unwind time tau_unwind ≈ |n_after|/u, not T - t, because positions left at the end close free at the last price. Avellaneda-Stoikov then gives a per-share inventory premium for adding a same-direction block of size q on top of n: ≈ gamma*sigma^2*tau_unwind*(n + q/2), which is linear in occupied inventory. The bot's `risk_aversion` term ($ per unit of vol × sqrt(unwind ticks)) is a variant of this.
- Guéant's convexity in block size means a block that pushes |n| toward L carries a larger premium per share than one taken from flat. This is a second, independent reason (beyond the knapsack BP) for required edge to rise with occupancy.
- The hard bound Q in Guéant-Lehalle-Fernandez-Tapia matches the net limit: at the limit the bot simply cannot take same-side flow. The value of being one step away from the bound (ln v_q/v_{q+1}) blows up near Q, which is the market-making counterpart of BP rising steeply as n_after approaches L.

### Gaps
- None of these market-making models has lumpy, discrete, scheduled tender arrivals. The Poisson fill rates A*e^{-k*delta} describe small fills, so the formulas give shape (linear skew, time dependence) but not calibrated $ values for 55k blocks.

---

## Q4. Exploiting direction: an opposite-side offer frees room

### Takeaway
On a signed (net) capacity, an offer opposite to the current position has a negative bid price: it gives back room. Its required edge should fall by the value of the room it frees, plus the unwind cost it saves, because internal crossing is free.

### Cited Findings
- The optimal acceptance condition r >= J(x) - J(x - A_j) holds for any consumption vector A_j (Talluri & van Ryzin, Prop. 1). Nothing in the derivation needs A_j to be non-negative, so an item that adds capacity (A_j < 0) gets a negative opportunity cost. — [Talluri & van Ryzin 1998](https://business.columbia.edu/sites/default/files-efs/pubfiles/3933/network_revenue_management.pdf) (extension to negative A_j is my inference)
- Avellaneda-Stoikov: a long agent's reservation price is below mid, so it will sell more cheaply to reduce inventory. That is the market-making form of "opposite flow is worth more". — [Avellaneda & Stoikov 2008](https://www.math.nyu.edu/~avellane/HighFrequencyTrading.pdf)

### Inferences
- Toy DP (my computation): at n = -55k, a BUY block (which offsets the short) has BP of -$0.009/share at u = 3k/s and -$0.028/share at u = 1k/s. That mirrors the +BP of the same-direction case at n = 0.
- Rule. For an opposite-direction offer with |d*q + n| < |n|, required edge = base - BP_freed(n) - c_unwind_saved, where c_unwind_saved is the per-share execution cost (spread, fees and impact) of the inventory that would otherwise go to the market, on the min(|n|, q) shares crossed internally. Value the crossed shares at mid, not at a walk of the book. Watch the cross-stock case: the net limit sums signed positions across the 3 stocks, so a SELL in stock B offsets a long in stock A for limit purposes. The price risk does not offset, though. Room is freed, but stock A still has to be unwound and stock B's short added on top.
- Because offers arrive in both directions at roughly equal rates, a position close to +L blocks half of future offers (same-direction ones) and makes the other half more attractive. Expected BP must therefore be averaged over direction, which the toy DP does (each direction with probability 1/2).

### Gaps
- I found no literature treating cross-asset netting under an aggregate net limit with separate per-asset price risk. The above comes from my own model.

---

## Q5. Practical, testable rule shapes; unwind speed as a capacity lever; auction gating

### Takeaway
Replace the constant $0.01 floor with required_edge = base + BP(n_after, u_hat, F, W, T_left)/q, using the Q2 closed form or a precomputed DP table. Gate auction bids by expected win value against the reservation cost of the room they hold while pending. Unwind fast only down to the "fit threshold" |n| = L - q, then slow down. Competitive-ratio rules with exponential thresholds exist but assume small items, so use them only for the shape.

### Cited Findings
- Online knapsack threshold (Zhou, Chakrabarty & Lukose, WWW 2008): Psi(z) = (U*e/L)^z * (L/e), where z is the fraction of capacity filled; pick item t only if v(t)/w(t) >= Psi(z(t)). It is (ln(U/L) + 1)-competitive and no online algorithm does better. Assumptions: (i) w(t) is much smaller than the budget, and (ii) L <= v/w <= U. — [Zhou, Chakrabarty & Lukose 2008 (pdf)](https://archives.iw3c2.org/www2008/papers/pdf/p1243-zhou.pdf)
- Balance-type rules use reduced reward r*(1 - g(remaining fraction)) with g(x) = e^{-x}. This is a multiplicative penalty that rises as capacity is used. — [Goyal, Iyengar & Udwani, arXiv:2002.02430](https://arxiv.org/pdf/2002.02430)
- Re-solving or Bayes-selector rules keep regret constant independent of horizon and budget, and their performance "does not depend on fine tuning" of the estimators. — [Vera & Banerjee, arXiv:1901.05028](https://arxiv.org/abs/1901.05028)
- The block risk-liquidity premium falls as the liquidation window lengthens (Corollary 3.1) and is convex in size (Prop. 3.3). Trading faster to free room therefore has an increasing marginal execution cost. — [Guéant, arXiv:1210.6372](https://arxiv.org/pdf/1210.6372)
- Repo context (not a published source): an auction "flow" pricing test raised auction wins from 1% to 14% but cost $4.1-7.3k/heat because "the 55k block crowds fixed tenders out of the net limit". A 45k net gate removed the loss without adding gain (-$0.8k, t -1.4). — `config/liability.toml` comments in this repo (`liquidity_model` block)

### Inferences (concrete rules to A/B test; my derivations)
1. R1 occupancy-dependent bid price (replaces the $0.01 floor):
   accept if edge_ps >= base + BP/q, where
   BP = S_hat * (1 - exp(-(lambda/2) * min(T_offer_left, max(0, tau_free - W))))
   tau_free = F + max(0, |n_after| - (L - q_typ)) / u_hat
   - S_hat is the expected positive surplus of a future offer. Estimate it from logged tenders as mean(max(edge*q - cost, 0)) over all offers, auctions included at their realistic win-adjusted value. The brief's figures (accepted average about +$0.55/share, about $29k) give S_hat of roughly $10-16k, depending on the fraction acceptable.
   - u_hat is the forecast unwind rate (the existing `liq_prior` and EWMA). F is the expected freeze: booking delay plus any pending tender in the same stock.
   - Same direction (|n_after| > |n|): BP > 0. Opposite direction: subtract BP_freed (Q4).
   - The expected size of the change: at u = 3k/s it adds roughly $0.01-0.05/share and will rarely change a fixed-tender decision. It changes auctions and marginal fixed tenders when u is low (dried liquidity) or F is large.
2. R2 auction gating by reservation cost. A pending auction bid holds about q of room for its window W_a (≈30 s), plus the respect-windows freeze on that stock, whatever the outcome. Bid only if
   p_win * e_auction * q >= P(same-direction offer arrives during the window and is not rescued) * S_hat + p_win * BP_after_win.
   Worked example: p_win ≈ 0.14 (the repo's observed win rate), e_auction ≈ $0.10, q = 55k gives an expected gain of about $0.8k. Over a 30 s window, P(a same-direction offer) = 1 - exp(-30/66) ≈ 0.37. If even a third of those are blocked and not rescued, the reservation cost is about 0.12 * S_hat ≈ $1.2-1.9k, which exceeds the gain. Rule of thumb: bid on an auction only if (a) a same-direction block would still fit after the reservation (|n ± q| <= L - q_typ, i.e. |n_after| <= 45k), or (b) the auction offsets current inventory, or (c) T_offer_left < ~30-40 s (few future offers to protect).
3. R3 two-speed unwind toward the fit threshold. Capacity value accrues only while |n| > L - q_typ (≈45k): the marginal value of freeing room is about (lambda/2) * S_hat ≈ $150-250 per second of earlier fit. Below that level, freeing room is worth about 0. So unwind at the aggressive rate until |n| <= L - q_typ, then switch to the cost-minimising (passive, lower participation) rate.
   - Trade-off: pay extra impact Δc per share on the x = |n| - (L - q) excess shares to save Δtau seconds, which is worthwhile if x*Δc < (lambda/2) * S_hat * Δtau (inside the window not rescued by W). Example: x = 10k shares, speeding from 1k/s to 3k/s saves about 6.7 s, worth roughly $1-1.7k, so up to about $0.10-0.17/share of extra impact is justified on those 10k shares only. Guéant's convexity means the extra cost per share rises with speed, so the optimum is interior.
4. R4 end-of-heat decay. BP goes to 0 as T_offer_left falls below tau_free - W. Combined with the free close at the last price, late blocks should be judged on edge vs. expected last price plus holding risk only (consistent with the existing `hold_valuation`).
5. Exact alternative to R1/R2: precompute a DP table V_t(n, f) (as in the toy model) each second or each tick from calibrated (lambda, edge distribution, u_hat, F), and use BP = V(cont) - V(after). This is cheap: about 201 position states × 420 s took ~10 s in pure Python with a freeze dimension, and much less with numpy or by precomputing per heat. Re-solving keeps it adaptive (Vera-Banerjee; Talluri-van Ryzin's "resolved frequently").
- Robustness with about 11 arrivals: R1 and R2 rely only on lambda, S_hat, u_hat and F. Errors in lambda or S_hat move BP proportionally, but BP itself is small relative to fixed-tender edges, so the risk of wrongly rejecting a good fixed tender is low. Poisson arrivals are a fair approximation (the case has roughly regular arrivals every 30-35 s). Regular arrivals make the "offer arrives during the exposed window" probability closer to the deterministic min(1, window/33), slightly higher than the exponential form for windows under 33 s. Test with paired seeds plus a holdout, following the repo's fair-testing rule (keep only changes with t >= 2 and holdout confirmation).
- Shape vs. the brief's proposed formula, "base + lambda*(net used/limit)*(time factor)": the theory gives a convex, step-like dependence on n_after. It is about zero while |n_after| <= L - q and then rises with the exposed window (|n_after| - (L - q))/u. It is not linear in net used. Time dependence is roughly flat until the last ~1-2 inter-arrival gaps, then drops to 0.

### Gaps
- The Zhou et al. exponential threshold assumes items much smaller than capacity. Here each block is about 55% of the net limit, so its competitive guarantee does not apply and its threshold would be overly conservative. I found no competitive-ratio result for items that are a large fraction of capacity, with reusable capacity.
- No source gives p_win for RITC auctions or the realized distribution of tender edges beyond the figures in the brief and repo comments. S_hat and p_win must be estimated from the bot's own logs.
- The toy DP ignores the gross limit (250k, rarely binding with 55k blocks), per-stock price risk, other offers arriving during a room-wait, and the auction/fixed mix. Its numbers indicate magnitude and shape, not calibrated values.
