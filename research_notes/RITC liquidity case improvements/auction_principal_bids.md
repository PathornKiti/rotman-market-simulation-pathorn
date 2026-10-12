# Auction theory and principal-bid pricing: bidding rules for the two RITC tender auction types

Notation used throughout these notes (ours, not from the sources):
- v = true per-share exit value of the block to us (what we actually realise unwinding it). BE = our book-walk break-even estimate. Field evidence: BE is about $0.19/share too pessimistic, so v is roughly BE + 0.19.
- b = our bid per share. R = hidden reserve. Q = block size (50-59k). N = number of rival teams.
- "Offset" = distance of a price from the mid, signed so that larger means more favourable to the tender issuer.
- Profit if filled = (v - b) * Q for a buy-side tender. Sell side is the mirror image.

Fit flags: [FEW-OBS] means the result is usable with 1-20 observations. [UNK-R] means it works with a reserve of unknown distribution. [UNK-N] means it does not need N. [NEEDS-PRIOR] means it requires a distribution or a large sample.

---

## Q1. Optimal bid against a hidden reserve with unknown distribution (competitive auction: fill at own price if better than the reserve, no rivals in the payoff)

### Takeaway
Against a pure hidden reserve, each fill is at our own price, so this is a one-sided posted-price problem. If the reserve were known, the best bid is the reserve itself (plus one tick), whatever our value is. The value estimate only caps the bid (do not bid past v minus capacity cost). Distribution-free rules exist: minimax-regret gives either a deterministic midpoint rule or a randomized bid with density 1/(v-b). For a reserve that is constant (or a constant offset) per tender type, a Kleinberg-Leighton staircase learns it with O(log log n) regret, and that rule never uses v. So it is fully insensitive to bias in the value estimate.

### Cited Findings
- Bergemann & Schlag set up a seller who "only knows the support of the possible valuations and has no further distributional information" and minimises regret. They show that "the pricing policy hedges against uncertainty by randomizing over a range of prices." [UNK-R][FEW-OBS] — [Bergemann & Schlag, Pricing without Priors, Cowles DP 1625 (2007)](https://cowles.yale.edu/sites/default/files/2022-08/d1625.pdf)
- Model: the seller's profit is pi(p,v) = (p - c)*1{v >= p}, with buyer value v in [v_low, 1]. Regret is r(p,v) = max{v - c, 0} - (p - c)*1{v >= p}. Positive regret comes from two sources: selling too cheaply (r = v - p) or failing to sell when p > v > c (r = v - c). — [Bergemann & Schlag 2007](https://cowles.yale.edu/sites/default/files/2022-08/d1625.pdf)
- Proposition 1 (unique minimax-regret randomized policy): define the lower bound as a* = max{v_low, c + (1 - c)/e}. Prices are drawn on [a*, 1] with CDF Phi(p) = 1 + ln((p - c)/(1 - c)), equivalently density 1/(p - c). There is a point mass at p = v_low iff v_low > c + (1 - c)/e. The maximal regret is (a* - c), which equals (1 - c)/e when v_low is low. The least-favourable demand is F(v) = 1 - (a* - c)/(v - c) on [a*, 1), with a mass point at 1. — [Bergemann & Schlag 2007](https://cowles.yale.edu/sites/default/files/2022-08/d1625.pdf)
- Corollary 1 (pure strategies only): the minimax-regret deterministic price is p = (1 + c)/2 if v_low < (1 + c)/2, otherwise p = v_low. The regret is "naturally higher under the restriction to pure strategies." — [Bergemann & Schlag 2007](https://cowles.yale.edu/sites/default/files/2022-08/d1625.pdf)
- The maximin-profit criterion (as opposed to minimax regret) puts all mass on p = v_low: "the seller is exclusively concerned with missing sales." In other words, maximin ignores forgone surplus. — [Bergemann & Schlag 2007](https://cowles.yale.edu/sites/default/files/2022-08/d1625.pdf)
- The minimax-regret policy is also Bayes-optimal against the least-favourable prior. "In this sense it is as if the minimax regret approach selects a specific prior." — [Bergemann & Schlag 2007](https://cowles.yale.edu/sites/default/files/2022-08/d1625.pdf)
- Online posted price with only accept/reject feedback (exactly our censored win/lose signal). Kleinberg & Leighton consider valuations "all equal to some unknown number p," valuations that are i.i.d. from an unknown distribution, and adversarial valuations. Their bounds: identical values give regret Theta(log log n) with a deterministic strategy. Random values give O(sqrt(n log n)) with lower bound Omega(sqrt(n)). Worst-case values give O(n^(2/3) (log n)^(1/3)) with lower bound Omega(n^(2/3)). [UNK-R] — [Kleinberg & Leighton, The Value of Knowing a Demand Curve](https://www.cs.cornell.edu/%7Erdk/papers/oppa.pdf)
- K-L staircase for an identical (fixed) value: "keeps track of a feasible interval [a, b], initialized to [0, 1], and a precision parameter eps, initialized to 1/2." The seller "offers the prices a, a+eps, a+2eps, ... until one of them is rejected." The new interval is [a+k*eps, a+(k+1)*eps] and the new precision is eps^2. At most one rejection per phase, and with no randomness "every response gives the seller perfect information about a lower or upper bound on p." [FEW-OBS][UNK-R] — [Kleinberg & Leighton](https://www.cs.cornell.edu/%7Erdk/papers/oppa.pdf)
- Repeated first-price auctions with censored feedback (the bidder sees only the winning bid) admit O(sqrt(T) log^2.5 T) regret for stochastic values. The authors exploit first-price structure rather than generic UCB/Thompson sampling. Note that this is richer feedback than ours (we only see win/lose). — [Han, Zhou, Flores, Ordentlich, Weissman, arXiv 2003.09795](https://arxiv.org/abs/2003.09795v5)

### Inferences
- **Mapping to our problem (derived by change of variables p' = -b, c' = -v, value' = -R, so it is exact):** we "sell" a fill at price b to a reserve R in [R_lo, R_hi]. Here R_lo is the least favourable reserve to us, R_hi the most favourable, and we profit iff b >= R (in issuer-favourable offset space).
  - Deterministic minimax-regret bid: **b = (v + R_lo)/2**, the midpoint between our value and the most demanding plausible reserve, if R_hi > (v + R_lo)/2. Otherwise b = R_hi, which always wins. Worst-case regret is (v - R_lo)/2.
  - Randomized minimax-regret bid: draw b on [R_lo, v - (v - R_lo)/e] with density 1/(v - b). Equivalently, set u ~ U(0,1) and b = v - (v - R_lo)*exp(-u). Worst-case regret is (v - R_lo)/e, about 0.37(v - R_lo). This beats the deterministic 0.5(v - R_lo).
  - Bias sensitivity: both rules move by 1/2 to 1 times any error in v. They are not bias-insensitive.
- **Bias-insensitive rule (recommended for the competitive type):** since payment = own bid, the profit-maximising bid against a known fixed R is R + 1 tick, independent of v. So learn R directly in offset space, using a K-L-style staircase per tender type:
  - start at the current conservative offset;
  - after each loss, step the offset up by d;
  - after the first win, the reserve is bracketed in [last losing offset, winning offset];
  - then halve the step and bid at the winning side of the bracket;
  - cap every bid at v_hat - capacity cost (Q4).

  This uses v only as a participation cap, so the ~$0.19 pessimism in BE only matters if the cap binds. With ~4 auctions per heat, the "few observations" regime is exactly where K-L's O(log log n) fixed-value bound helps. It only applies if the reserve is (close to) a fixed offset from mid. If reserves are random i.i.d., the cost of learning rises to sqrt(n), and with n of about 20-50 auctions total, learning cannot converge. In that case prefer the minimax-regret rule above with a conservative R_lo.
- Empirical non-parametric bracket (no normal assumption): keep for each tender type the max losing offset L* and the min winning offset W*. The reserve offset lies in (L*, W*] if reserves are constant. If later data contradict this (a win below L*), reserves are random. In that case switch to a quantile rule: bid at the empirical win-rate-maximising offset among tried offsets, subject to the cap.
- Our 2 wins out of 18, both on one tender id, are consistent with a reserve that sits close to our current offset for that tender type and well inside it for others. This is untested inference.

### Gaps
- No source found that gives the minimax-regret rule directly for a *bidder* facing a hidden reserve. The mapping above is my exact algebraic transformation of Bergemann-Schlag, not a quoted result.
- Bergemann & Schlag (2007 JEEA / "Robust monopoly pricing") give rules for a neighbourhood of a model distribution. I did not open that paper, so no formula is quoted.
- I found no source on how the RITC server sets the hidden reserve (fixed offset vs random draw). This determines whether the K-L fixed-value regime or the random-value regime applies.
- Turnbull/interval-censored NPMLE would be the standard non-parametric estimator for censored reserve data. I did not open a source for it.

---

## Q2. First-price common value with N symmetric rivals and the winner's curse (winner-take-all type)

### Takeaway
In a winner-take-all first-price tender, the winner is the team with the most optimistic estimate of exit value. Conditioning on winning lowers the expected value by eps*(N-1)/(N+1). Here eps is the half-width of estimate errors across teams, and the factor tends to the full eps as N grows. The equilibrium bid is approximately the signal minus eps. With private values instead, the rule is b = v*(N-1)/N. With many teams (N about 10-30), shade by about one full "estimate-error half-width" below your own value estimate, and more if your estimate is systematically optimistic.

### Cited Findings
- Private values, uniform: "in equilibrium, we each bid half our value. More generally, with N bidders, bids = v*(N-1)/N." Classroom 3-bidder data had a best-fit "bid = .83*value" vs the equilibrium .67. — [MIT 15.025 Lecture 16 (OCW)](https://ocw.mit.edu/courses/15-025-game-theory-for-strategic-advantage-spring-2015/dceb76046c7e0d5fb3f5a150adf7fac0_MIT15_025S15_Lec_16.pdf)
- Common value with signals x ~ U[x0 - eps, x0 + eps]: E[x0 | own signal is highest of n] = x - [(n-1)/(n+1)]*eps. "Bidding above E[xo|X = x1n] results in negative expected profit" when the high-signal holder usually wins. — [Kagel & Levin, Common Value Auctions and the Winner's Curse (survey)](https://www.asc.ohio-state.edu/kagel.4/CVsurvey.short.PDF)
- The RNNE bid function is "well approximated by b(x) = x - eps" in the interior region; the exponential correction term vanishes quickly. The adverse-selection correction alone is "60% of eps" with n = 4 and "75% of eps" with n = 7. "Strategic considerations account for the rest of the bid factor; 2eps/(n+1)." The correction "is relatively large and increasing with increases in the number of bidders." [UNK-N partially: the total bid factor is about eps regardless of n] — [Kagel & Levin survey](https://www.asc.ohio-state.edu/kagel.4/CVsurvey.short.PDF)
- In experiments, "59% of all bids and 82% of the high bids were above E[xo|X = x1n]," which is the winner's curse. Inexperienced bidders systematically overbid. — [Kagel & Levin survey](https://www.asc.ohio-state.edu/kagel.4/CVsurvey.short.PDF)
- In common-value auctions the winner's curse ranking is "English > SPA > FPA," and revenue equivalence holds only under conditions. In the "wallet game," the advice is to "Avoid the winner's curse." — [MIT 15.025 Lecture 16](https://ocw.mit.edu/courses/15-025-game-theory-for-strategic-advantage-spring-2015/dceb76046c7e0d5fb3f5a150adf7fac0_MIT15_025S15_Lec_16.pdf)
- Naive bidding (ignoring conditioning on winning) fits lab data better than rational models. "Naive bidding results in excessively high bids (a winner's curse)." — [Holt & Sherman (2000), Risk Aversion and the Winner's Curse](https://www.cs.princeton.edu/courses/archive/spr08/cos444/papers/HoltSherman00.pdf)
- Field principal-bid auctions (4 brokers, 83 packages): winning bids were only 6 bps (conservative Broker A) to 20 bps (aggressive Broker D) below the second-best bid. Loser A's bid was 30 bps above the winning bid. The authors note Broker D's aggressive wins "might be" a winner's curse but could reflect inventory crosses. — [Kavajecz & Keim, JFQA 2005](https://faculty.wharton.upenn.edu/wp-content/uploads/2014/03/KK_JFQA2005.pdf)

### Inferences
- **Rule of thumb for the winner-take-all type:** b = v_hat - eps*(N-1)/(N+1) - 2eps/(N+1), which is about v_hat - eps for N >= 5. Here eps is the half-width of the cross-team error in exit-value estimates.
  - If most teams use similar book-walk estimates, eps is small. Rivals' estimates then differ mainly through their margins, which makes it closer to private values. The shading then comes from (N-1)/N on the *surplus*, not the value.
  - Our v_hat is biased low by ~$0.19. That bias already acts as about $0.19 of protective shading, so adding a further eps on top double-counts it.
- Because the common-value correction rises with N toward a full eps, it is roughly [UNK-N]: if N >= 5, assume the full eps.
- With many teams and a hidden reserve, a winner-take-all auction is won by the most aggressive team. Our win rate will be low unless our true v (inventory crossing, better unwind) exceeds rivals'. This matches KK's finding that winners are brokers with crossing inventory.

### Gaps
- Lauermann et al. (2023, Econometrica), "Bidding in Common-Value Auctions With an Unknown Number of Competitors," is directly relevant to [UNK-N]. The page returned 403, so its results are not quoted.
- No source opened gives the joint equilibrium for "first-price plus hidden reserve plus common value."
- The cross-team estimate dispersion eps is unknown and must be estimated from our own data, for example the gap between our bid and the realised fill price on winner-take-all tenders we lose, if that is ever revealed.

---

## Q3. Principal-bid / risk-bid pricing for block trades (how dealers set the price)

### Takeaway
Dealers price a risk bid as mark-to-market minus a risk-liquidity premium. The premium is permanent impact (k q^2/2), plus instantaneous execution cost, plus a risk term. In the CARA framework with Almgren-Chriss-style execution, the risk term is about gamma*sigma^2*q^2*T/6. Empirically the premium is dominated by impact, not risk. Dealers bid more aggressively when the block crosses existing inventory, when they have a longer horizon, and when the flow is certified uninformed. Book-walk (displayed-liquidity) costs overstate true block costs by 3-5x.

### Cited Findings
- Guéant defines the block price via certainty equivalent: sup E[-exp(-gamma X_T)] = -exp(-gamma P(q0)). This gives P(q0) = q0*S0 - psi*q0 - (k/2)*q0^2 - inf_rho J(rho). The risk-liquidity premium is l(q0) = q0*S0 - P(q0). [NEEDS-PRIOR: impact parameters] — [Guéant, Execution and block trade pricing with optimal constant rate of participation, arXiv 1210.7608](https://arxiv.org/pdf/1210.7608)
- With L(rho) = eta*rho^(1+phi) and constant market volume V: J(rho*) = eta*rho*^phi*q0 + (gamma/6)*sigma^2*q0^3/(rho* V). The optimal participation is rho* = (gamma sigma^2 q0^2 / (6 eta phi V))^(1/(1+phi)). The resulting premium is l_POV(q0) = k q0^2/2 + psi q0 + eta^(1/(1+phi)) (gamma sigma^2/(6 phi V))^(phi/(1+phi)) q0^((1+3phi)/(1+phi)) + phi eta^(1/(1+phi)) (gamma sigma^2/(6 phi V))^(phi/(1+phi)) q0^((1+3phi)/(1+phi)). The terms are permanent impact, instantaneous impact and risk. — [Guéant arXiv 1210.7608](https://arxiv.org/pdf/1210.7608)
- Comparative statics: the premium rises with gamma, sigma and eta, falls with V, and is convex/superlinear in q0. — [Guéant arXiv 1210.7608](https://arxiv.org/pdf/1210.7608)
- Calibrated examples (Total/Axa/Danone, 10-15% of ADV, gamma = 3e-6): the premium is 45-100 bps. Permanent impact is 29-77 bps, instantaneous impact 8-14 bps, and the risk component only 4.7-8.7 bps. The optimal participation is 11.6-28.1%. "The permanent market impact component represents a large part" of the premium. — [Guéant arXiv 1210.7608](https://arxiv.org/pdf/1210.7608)
- Blind principal-bid auctions: the package auction cut trading costs by 62 bps vs a benchmark, "a 48% reduction in transaction costs." The savings "hinge upon the liquidity providers (bidders) possessing both inventory positions with which they can cross many of the trades within the package and a longer trading horizon," and to a lesser extent superior trading ability. — [Kavajecz & Keim, JFQA 2005](https://faculty.wharton.upenn.edu/wp-content/uploads/2014/03/KK_JFQA2005.pdf)
- Inventory effect: "a broker with a surplus of existing buy (sell) transactions would submit a lower (i.e., more aggressive) bid for a package that contained predominantly sell (buy) transactions." More names in a package give a diversification effect that lowers bids. — [Kavajecz & Keim 2005](https://faculty.wharton.upenn.edu/wp-content/uploads/2014/03/KK_JFQA2005.pdf)
- Field numbers: benchmark costs were 1.27-1.67% across brokers. Winning bids were 49-81 bps below benchmark. Data: 83 packages from Jul 1998 to Jul 2000, mean package $51-121M, 76-200 stocks. — [Kavajecz & Keim 2005](https://faculty.wharton.upenn.edu/wp-content/uploads/2014/03/KK_JFQA2005.pdf)
- Upstairs block execution costs "are on average only 20% (35%) as large as they would be if block trades were executed against displayed (displayed and hidden) liquidity in the downstairs limit order book." The data support Seppi's (1990) certification of trades as uninformed. — [Bessembinder & Venkataraman, JFE 2004 (SMU ScholarWorks abstract)](https://scholar.smu.edu/business_finance_research/41)
- Engle, Ferstenberg & Russell jointly model expected cost and risk of execution. They give a mean-variance frontier of strategies and a "liquidation value at risk (LVaR)" built from the conditional distribution of costs. — search-result summary only, abstract page not opened: [SSRN 1211162](https://papers.ssrn.com/abstract=1211162)
- Almgren & Chriss (Risk, 2003, "Bidding principles") reportedly annualise the price impacts and variances of principal-bid trades into an information-ratio measure, used to set hurdles below which bids at a given discount should not be accepted. — the page failed to load, so this rests on the search snippet only: [Risk.net](https://www.risk.net/infrastructure/trading-platforms/1530429/bidding-principles)

### Inferences
- **Dealer-style bid for our block (per share):** b = mid - [k*Q/2 + psi + inst(Q, T) + (gamma/2)*sigma^2*Q*T/3] - capacity_cost(Q4) - crowding(Q5). The linear-unwind variance term is sigma^2*Q^2*T/3 in total dollars, so (gamma/6)*sigma^2*Q*T per share, consistent with Guéant's J term.
  - With T = 20-60 s, the risk term is tiny for any reasonable gamma, so the price should be set almost entirely by realised impact.
  - The book-walk BE effectively uses displayed-depth impact. Bessembinder-Venkataraman (20-35% of book-walk cost) and our own +$0.19 measured error both say book-walk overstates the true unwind cost.
  - Recommendation: calibrate psi and k from our own realised unwind P&L per tender (regress realised exit vs Q, direction and spread) rather than from the book walk.
- **Inventory crossing (KK):** when the tender direction offsets our current net position (e.g., we are long and the tender asks us to sell), unwind cost and capacity use both drop. Bid more aggressively there, and less aggressively when the tender adds to existing exposure.
- **Information/certification (Seppi, B-V):** RITC tenders are exogenous (not informed), so adverse-selection components are near zero. Only the impact and crowding terms matter.

### Gaps
- Almgren-Chriss "Bidding principles" (Risk 2003): the text and its exact information-ratio hurdle formula could not be accessed.
- Engle-Ferstenberg-Russell: full paper not opened, so no formulas are quoted.
- No source found on how dealers adjust the risk price for capacity already used (balance-sheet limits). Q4 covers this via revenue-management analogues.

---

## Q4. Bidding when a win consumes scarce capacity (opportunity cost)

### Takeaway
The revenue-management answer is bid-price control: accept a request iff its revenue is at least the opportunity cost of the capacity it consumes, J(x) - J(x - A). In bidding terms, subtract the expected displaced value of future fixed-price tenders from the bid. Budget-pacing theory gives the same structure as a shadow price mu: bid v/(1+mu), updated online from realised consumption.

### Cited Findings
- Bid-price control: thresholds are set per resource, and a product "is sold only if the offered fare exceeds the sum of the threshold prices of all the resources needed to supply the product." Bid-price control is "not optimal in general" but "asymptotically optimal" when capacities and volumes are large, "provided the right bid prices are used." — [Talluri & van Ryzin, An Analysis of Bid-Price Controls for Network Revenue Management, Mgmt Sci 1998](https://business.columbia.edu/sites/default/files-efs/pubfiles/3933/network_revenue_management.pdf)
- Exact DP: J_k(x) = max E[R_k u_k + J_{k-1}(x - A u_k)]. A request is optimally accepted when its revenue covers the "opportunity cost of the itinerary, J_k(x) - J_k(x - A_j)." — [Talluri & van Ryzin 1998](https://business.columbia.edu/sites/default/files-efs/pubfiles/3933/network_revenue_management.pdf)
- Budget pacing: bid = v/(1+mu), where mu is the "shadow price reflecting the marginal opportunity cost of the budget constraint." Update rule: mu_{t+1} = Proj_[0, mu_bar](mu_t - eps*(rho - z_t)), with rho the target consumption rate and z_t the realised consumption. The guarantee is O(T^(3/4)) regret vs the best fixed multiplier in stochastic monotone settings. — [Budget Pacing in Repeated Auctions, arXiv 2205.08674](https://arxiv.org/html/2205.08674v6)
- Balseiro & Gur introduced adaptive pacing for sequential auctions with "uncertainty about future bidding opportunities," where bidders know neither their own value distribution nor rivals' budgets. — [Stanford GSB page for Balseiro & Gur, Mgmt Sci 2019](https://www.gsb.stanford.edu/faculty-research/publications/learning-repeated-auctions-budgets-regret-minimization-equilibrium)

### Inferences
- **Opportunity-cost-adjusted bid (bid-price control form):** b = v - m - c_cap.
  - c_cap = (1/Q) * E[value of fixed tenders displaced while the block occupies 50% of the limit].
  - Approximately, c_cap ≈ lambda_F * T_unwind * P(blocked | arrives) * V_F / Q, where lambda_F is the arrival rate of fixed tenders, V_F is about $27k, and T_unwind is 20-60 s.
  - Illustration only: with lambda_F*T_unwind*P(blocked) = 0.3 and Q = 55k, c_cap ≈ 0.3*27,000/55,000 ≈ $0.15/share. This is the same order as the $0.19 BE bias. That would explain why "bidding closer to mid" (dropping both cushions) lost $4-7k per heat.
  - c_cap should be state-dependent (J(x) - J(x - A)): near zero late in the heat (no time for more fixed tenders), when current inventory is near zero and the block fits without blocking, and when the block crosses existing inventory (it frees capacity). It is high early, or when inventory is already large.
- **Pacing form:** instead of estimating lambda_F, keep mu per heat, bid v/(1+mu), and raise mu whenever a won block actually caused a fixed tender to be declined. This is [FEW-OBS]-friendly in structure but converges slowly (T^(3/4)), so a fixed hand-set c_cap schedule by time-remaining is likely more practical at ~4 auctions per heat.
- **Crowding (competitive type):** all winners unwind the same block, so realised v falls with the number of winners K. Use v(K_expected) rather than v(1), where K_expected is driven by how far our bid is inside the reserve. This is untested. Rule: in the competitive type, bidding just at the reserve wins alongside every other team that also clears it, so there is a crowding discount on top of c_cap.

### Gaps
- No source found that directly treats sequential first-price auctions where winning blocks capacity for a *different* (fixed-price) revenue stream. The mapping to bid-price control is my inference.
- The first-price budget-constrained regret paper (arXiv 2205.14572) appeared in search but was not opened.

---

## Q5. Learning a reserve from censored outcomes with very few observations (robust / quantile, not Bayesian-normal)

### Takeaway
With only win/lose feedback and fewer than 20 observations, two robust tools fit. (1) If the reserve is a fixed offset per tender type, a K-L staircase/bracket brackets it with at most one loss per phase and needs no distributional assumption. (2) If it is random, apply minimax-regret randomization over [R_lo, v - (v - R_lo)/e], which needs only bounds. Both avoid the overshading failure mode of a parametric (normal) posterior plus a Myerson inverse-hazard bid, which is sensitive to tail-shape misspecification.

### Cited Findings
- The K-L identical-value staircase achieves O(log log n) regret, and "no pricing strategy can achieve regret o(log log n)." Each response gives "perfect information about a lower or upper bound." [FEW-OBS][UNK-R] — [Kleinberg & Leighton](https://www.cs.cornell.edu/%7Erdk/papers/oppa.pdf)
- For i.i.d. random values, regret is at least Omega(sqrt(n)) even under regularity (unique maximiser of x*Pr(v >= x), negative second derivative). So learning a random reserve distribution from binary feedback is intrinsically slow. — [Kleinberg & Leighton](https://www.cs.cornell.edu/%7Erdk/papers/oppa.pdf)
- The minimax-regret policy needs only the support. It is "as if the minimax regret approach selects a specific prior" (the least-favourable one), so it is a robust replacement for a hand-picked Bayesian prior. — [Bergemann & Schlag 2007](https://cowles.yale.edu/sites/default/files/2022-08/d1625.pdf)
- Even with richer feedback (seeing the winning bid), optimal first-price learning regret is about sqrt(T) up to logs. — [arXiv 2003.09795](https://arxiv.org/abs/2003.09795v5)

### Inferences
- **Concrete few-observation procedure (per tender type, in offset space):**
  1. Keep L* = the most aggressive offset that lost and W* = the least aggressive offset that won.
  2. If W* exists and W* - L* <= one step, bid W* (capped by v_hat - c_cap - crowding). Otherwise bid min(L* + step, cap).
  3. After each loss, step up. After a win, set step = step^2 (relative) or halve it, K-L style.
  4. If a win ever occurs below L*, declare reserves random. Then switch to the minimax-regret randomized bid, with R_lo = the empirical 10th-percentile winning offset and v = v_hat - c_cap.
- The previously rejected Bayesian censored-normal plus Myerson approach "overshaded." That is consistent with these theory results: the inverse-hazard bid depends on tail shape that 18 censored points cannot identify. A bracket/staircase rule depends only on order information, so it is robust to that failure.
- Fair-testing implication: the staircase is deterministic given outcomes, so A/B paired tests on sim heats with a fixed reserve process are straightforward. However, the sim's reserve model must match the real server's, or the result is meaningless.

### Gaps
- No source opened gives finite-sample (n < 20) guarantees beyond the K-L constants. All "few observations" claims rest on the log log n structure, which assumes a fixed reserve.
- How the actual RITC reserve is generated (fixed offset vs per-tender random vs tied to the live mid) is unknown. It is the single most important unknown for choosing between the staircase and minimax-regret rules.
