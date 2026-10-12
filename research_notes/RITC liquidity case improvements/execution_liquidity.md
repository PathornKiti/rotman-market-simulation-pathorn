# Unwinding large blocks with limit and market orders and forecasting liquidity during the unwind (execution_liquidity)

Scope: rules with closed forms or simple numerical procedures for (a) how deep to post vs inventory and time left and when to cross, (b) the block/continuous mix under a book that refills at a measured rate, (c) forecasting fill rate and pricing a block given that forecast, (d) when holding to a known free terminal close is better than unwinding, (e) which rules reduce slow-unwind losses without predicting direction.

Notation used across sections (our case): q = shares left (or lots of size Delta), T - t = seconds left in the unwind window or the heat, sigma = $/share per sqrt(s), A = fill rate of a resting order at the touch (lots/s), k = e-fold decay of fill rate per $ (or per tick) of distance from the touch, gamma = CARA risk aversion ($^-1), fee = $0.02/share on aggressive trades, terminal liquidation cost b = 0 (free close at the last traded price).

PDFs were downloaded from arXiv/NBER and read as text; formulas below are transcribed from those texts (equation numbers refer to the papers).

---

## Q1. Optimal liquidation with limit orders and fill probability: posting depth vs inventory and time left, and when to switch to market orders

### Takeaway
Guéant-Lehalle-Fernandez-Tapia (GLFT) give an exact solution (a linear ODE system) and closed-form approximations for the optimal ask depth delta*(t,q) of a liquidating trader with exponential fill intensity A*exp(-k*delta). Depth falls as inventory rises. When delta* turns negative, the authors say to send a market order. With a free terminal close (b = 0) the end-of-horizon quote is strictly positive, so the theory never asks you to cross at the end. That matches our passive-only last 90 s. The asymptotic formula gives an inventory threshold q*, below which you only rest and above which you cross. The threshold scales with sqrt(A), so it tightens automatically when absorption drops.

### Cited Findings
- **Model (GLFT).** Fill intensity lambda_a(delta) = A*exp(-k*delta), where delta = s_a - s is the ask's distance above the mid. Price dS = mu dt + sigma dW. The objective is sup E[-exp(-gamma*(X_T + q_T*(S_T - b)))], where b is a per-share cost for whatever is left at T. Orders are a constant lot size (ATS or a fraction of it). — [GLFT 2012, arXiv:1106.3279](https://arxiv.org/abs/1106.3279)
- **Exact solution (Prop. 1 and Thm 1).** Solve the linear ODE system w_q'(t) = (alpha*q^2 - beta*q)*w_q(t) - eta*w_{q-1}(t), with w_q(T) = exp(-k*q*b) and w_0 = 1. Here alpha = (k/2)*gamma*sigma^2, beta = k*mu, eta = A*(1+gamma/k)^-(1+k/gamma). The optimal ask depth is delta*(t,q) = (1/k)*ln(w_q(t)/w_{q-1}(t)) + (1/gamma)*ln(1+gamma/k). This is a matrix-exponential solve over q lots, cheap enough to precompute per tick. — [GLFT 2012](https://arxiv.org/abs/1106.3279)
- **Terminal behaviour.** As t -> T, delta*(T,q) = -b + (1/gamma)*ln(1+gamma/k) for every q. Away from the very end, a trader with a given q lowers quotes as T approaches "except near the final time T because a certain maximum discount b is guaranteed". — [GLFT 2012](https://arxiv.org/abs/1106.3279)
- **When to cross.** "negative quotes may appear ... when the quantity to liquidate is large compared to the remaining time, especially when (i) ... the liquidation cost b is high and/or (ii) when risk aversion and volatility are high". Also: "if a negative quote appears after some time, because of a slow execution, then sending a market order (with thus guaranteed execution) to reduce the inventory is a possible approximation of the model". The authors add that this ad hoc approximation ignores MO transaction costs. — [GLFT 2012](https://arxiv.org/abs/1106.3279)
- **Closed form, long horizon (Prop. 2; requires mu < gamma*sigma^2/2).** lim_{T->inf} delta*(0,q) = (1/k)*ln( A / ((k+gamma)*(0.5*gamma*sigma^2*q^2 - mu*q)) ). This is equivalent to (1/k)*ln( A / ((1+gamma/k)*(alpha*q^2 - beta*q)) ). The quote decreases in q, sigma and gamma, increases in A, and "does not depend on the liquidation cost b". — [GLFT 2012](https://arxiv.org/abs/1106.3279)
- **Closed form, no price risk (Prop. 3; sigma = mu = 0).** delta*(t,q) = -b + (1/k)*ln(1 + (eta^q/q!)*(T-t)^q / sum_{j=0}^{q-1} (eta^j/j!)*exp(-k*b*(q-j))*(T-t)^j) + (1/gamma)*ln(1+gamma/k). It is bounded below by -b + (1/gamma)*ln(1+gamma/k), so "quotes never go below -b". — [GLFT 2012](https://arxiv.org/abs/1106.3279)
- **Risk-neutral limit (gamma -> 0) = Bayraktar-Ludkovski.** delta*(t,q) = -b + (1/k)*ln(1 + ((A/e)^q/q!)*(T-t)^q / sum_{j<q} ((A/e)^j/j!)*exp(-k*b*(q-j))*(T-t)^j) + 1/k. In the risk-neutral case "the optimal quote ... does not converge to a limit value as T tends to +inf, but rather increases with no upper bound". — [GLFT 2012](https://arxiv.org/abs/1106.3279)
- **b -> infinity (must finish), sigma = mu = 0.** delta*_inf(t,q) = (1/k)*ln( A*(T-t) / ((1+gamma/k)*q) ). This follows from w_q = eta^q*(T-t)^q/q! and matches the paper's printed form. Expected trading curve V(t) = q0*(1 - t/T)^(1+gamma/k). This is the "worst case", in which quotes have no lower bound. — [GLFT 2012](https://arxiv.org/abs/1106.3279)
- **Numerical illustration.** Selling up to q = 6 lots in 5 min with mu = 0, sigma = 0.3 tick/sqrt(s), A = 0.1 /s, k = 0.3 /tick, gamma = 0.05 /tick and b = 3 ticks gives delta*(0,q) of 10.6, 7.9, 6.1, 4.8, 3.7, 2.8 ticks for q = 1..6. A drift of ±0.01 tick/s shifts these by about ±1-1.5 ticks (Table 1). — [GLFT 2012](https://arxiv.org/abs/1106.3279)
- **Bayraktar-Ludkovski (risk-neutral, exponential intensity lambda*exp(-kappa*s), lot Delta, r = 0, Prop. 4.1).** V(n*Delta,T) = (Delta/kappa)*log(sum_{j=0}^{n} (1/j!)*(lambda*T/(Delta*e))^j). s*(n*Delta,T) = (1/kappa)*(1 + log(1 + ((lambda*T/(Delta*e))^n/n!) / sum_{j<n} ((lambda*T/(Delta*e))^j/j!))). The fluid limit satisfies (x/kappa)*log(lambda*T/x) <= v(x,T) <= (lambda/(kappa*e))*T. — [Bayraktar & Ludkovski, arXiv:1105.0247](https://arxiv.org/abs/1105.0247)
- **General intensity shapes (Guéant-Lehalle).** The GLFT results extend beyond exponential intensities. The paper also gives an asymptotic quote for general Lambda (Prop. 4.2) and bounds on the value function (Prop. 5.1). — [Guéant & Lehalle, arXiv:1204.0148](https://arxiv.org/abs/1204.0148)
- **LO + MO with fees (Guilbaud-Pham).** Market orders are modelled as impulse controls at discrete dates, with cost c(e,p,s) = e*p + |e|*(s/2 + rho) + epsilon (half-spread + per-share fee + fixed fee). The result is a regime-switching quasi-variational system that is solved numerically (no closed form). — [Guilbaud & Pham, arXiv:1106.5040](https://arxiv.org/abs/1106.5040)
- **Cartea-Jaimungal (2015), LO + MO tracking an AC schedule.** The strategies "outperform the Almgren-Chriss price with an average savings per share of about one to two and a half times the spread", because limit orders "earn the spread and market orders ... keep the investor's inventory schedule on target". This is the same architecture we already run (rest, cross only the shortfall). — [Cartea & Jaimungal, Quant. Finance 15(8) 2015, abstract](https://ideas.repec.org/a/taf/quantf/v15y2015i8p1279-1291.html)
- **Bulthuis-Concha-Leung-Ward (AC with MO plus LO with uncertain fills).** Closed forms exist for constant, affine and linear fill uncertainty. As LO fill uncertainty -> infinity, the strategy collapses to MO-only AC with a terminal penalty: v*_t = (x_t - eta0/(eta1+beta1))*(2*beta - gamma) / (2*(eta1+beta1) + (T-t)*(2*beta - gamma)). With insufficient liquidation penalties there is "a finite maximum trading horizon in order for the value function to be finite". — [Bulthuis et al., arXiv:1604.04963](https://ar5iv.labs.arxiv.org/html/1604.04963)

### Inferences
- **Rule A (GLFT threshold for crossing, liquidity-aware).** Set the long-horizon delta* to 0 and solve for inventory: q* = sqrt( 2*A / (gamma*sigma^2*(1+gamma/k)) ), in lots, with mu = 0. Rest at the touch, or deeper if delta* > 1 tick, while q <= q*. Cross only q - q* (inside the existing 4-cent and 35% caps).
  - Unlike our time-based AC shortfall, q* has no clock in it. It depends on the measured fill rate A and the volatility sigma, and scales as sqrt(A)/sigma.
  - In the slow-heat cases A fell from about 3,000 to 700-900 sh/s. That shrinks q* by sqrt(800/3000) ≈ 0.52, so the rule crosses more exactly when the passive channel dries up. It reads a liquidity state and makes no direction forecast.
  - This is an inference from Prop. 2. It must be A/B tested, because crossing costs about half the spread plus $0.02 in expectation under a random walk.
- **Calibrating gamma.** gamma is not observable. Back it out so that q* reproduces our currently accepted risk: the existing hold_risk_budget = 40,000 $-sd is the de facto gamma. Calibrate A and k from our own resting fills (see Q3). k is the log-slope of fill rate vs distance from the touch in ticks.
- **Free close confirms the passive end-game.** With b = 0, delta*(T,q) = (1/gamma)*ln(1+gamma/k) > 0, so theory never crosses at the bell. The risk-neutral limit (BL) has quotes that grow without bound as T grows: with no risk aversion and a free terminal close there is no reason to pay to unwind. This supports `close_hold_ticks` and `hold_valuation`.
- **Depth is a monotone function of q.** When q is small relative to q*, the optimal ask sits above the touch (delta* > half-spread). With a few thousand shares left and time remaining, we could post 1-2 ticks deeper to capture more spread, instead of always resting at the touch or one tick inside. This is the GLFT/Avellaneda-Stoikov "small inventory, post deeper" effect.

### Gaps
- I could not open the full Cartea-Jaimungal 2015 paper or the CJP book chapter. I therefore cannot quote their explicit MO-region boundary (the inventory level at which an MO is sent as a function of time). Only the abstract was verified.
- None of the papers models a resting iceberg (display vs hidden size). GLFT assumes a constant lot size and fills unaffected by our displayed size.

---

## Q2. Transient impact and resilience: block vs continuous trading given a book that refills at a measured rate

### Takeaway
Obizhaeva-Wang (OW): with a block-shaped book of depth q per $ that refills exponentially at rate rho, the risk-neutral optimum is an initial block of X/(rhoT+2), a constant flow of rho*X/(rhoT+2) per unit time, and a final block of X/(rhoT+2). The share traded continuously is rhoT/(rhoT+2). Alfonsi-Fruth-Schied (AFS) generalise this to any book shape and get the same structure: one initial block, equal intermediate trades of xi0*(1-e^(-rho*tau)), one final block. The practical content is to measure the refill rate rho and size your sweeps to the refill, not to the visible depth.

### Cited Findings
- **OW book dynamics.** The ask side has depth q per unit price. A buy of size x0 moves the ask to F0 + s/2 + x0/q, with permanent part lambda*x0, where 0 <= lambda <= 1/q. The deviation from steady state decays as A_t = V_t + s/2 + x0*kappa*exp(-rho*t), with kappa = 1/q - lambda. New orders arrive at rate rho*q*D_t, where D_t = A_t - V_t - s/2. — [Obizhaeva & Wang, NBER WP 11444](https://www.nber.org/papers/w11444)
- **OW optimal strategy (Prop. 2, continuous-time limit).** x_0 = X0/(rhoT+2), Xdot_t = rho*X0/(rhoT+2) on (0,T), x_T = X0/(rhoT+2). Under the optimum, D_t is held constant at kappa*X0/(rhoT+2), so the replenishment flow rho*D_t is constant, and "a constant fraction of 1/kappa of the new sell orders is executed". — [Obizhaeva & Wang](https://www.nber.org/papers/w11444)
- **Fraction traded continuously.** The fraction is rhoT/(rhoT+2), "which increases with rho ... it is more efficient to use small trades when the limit order book is more resilient". At rho = 0 (no recovery) or rho = inf (instant recovery), "the cost of execution will be strategy independent". — [Obizhaeva & Wang](https://www.nber.org/papers/w11444)
- **OW calibration example.** X0 = 100,000, T = 1 day, depth q = 5,000, lambda = 1/(2q), rho = 2.231 (per day); the paper also tabulates the half-life log2/rho. Risk aversion makes the trader "trade more aggressively at the beginning". — [Obizhaeva & Wang](https://www.nber.org/papers/w11444)
- **AFS, general book shape, exponential volume recovery (Thm 4.1).** With N+1 trades at spacing tau, the initial order xi0 solves F^-1(X0 - N*xi0*(1-e^(-rho*tau))) = h1(xi0)/(1-e^(-rho*tau)), where h1(y) = F^-1(y) - e^(-rho*tau)*F^-1(e^(-rho*tau)*y). Intermediate orders are xi1 = ... = xi_{N-1} = xi0*(1-e^(-rho*tau)), and the final order is the remainder. The strategy is deterministic and all orders are on the same side. Each intermediate trade exactly re-consumes the volume that refilled since the last trade. — [Alfonsi, Fruth & Schied, arXiv:0708.1756](https://arxiv.org/abs/0708.1756)

### Inferences
- **Rule B (refill-sized crossing).** When we must cross, the AFS/OW optimum takes per tick about the volume that refilled since the last tick, i.e. (visible-depth deficit) x (1 - e^(-rho*1s)), plus one initial block sized X/(rhoT+2).
  - Estimate rho from the book's own recovery after our sweeps: regress log(depth deficit) on seconds since the sweep.
  - Example (assumed numbers): a refill half-life of 2 s gives rho ≈ 0.35/s. With T = 30 s, rhoT ≈ 10.4, so the initial block is X/12.4 ≈ 8% (about 4.4k of 55k), continuous trading is about 84%, and the final block is about 8%.
  - Our 35%-of-visible-depth cap per loop is a static proxy. A rho-based cap would cross more when the book refills fast and less when it does not.
- **Drop the final block.** Our terminal liquidation is free. The OW final block exists only because the problem forces completion at T, so in our setting it should be dropped and replaced by the free close. This is consistent with Q4.
- **Shape matters little.** OW and AFS say cost is strategy-independent at rho -> 0 or infinity. Guéant (Q3) bounds the gain from an optimised schedule over a constant participation rate at 14%. So schedule shape is second-order. The first-order decision is how much to cross at all.

### Gaps
- OW and AFS are risk-neutral and about market orders only. They do not cover resting limit orders being absorbed, which is our dominant channel. No paper I opened gives the joint optimum of an OW-resilient book plus passive fills.
- Gatheral's no-dynamic-arbitrage paper (power-law decay kernels) was not opened. Not cited.

---

## Q3. Liquidity forecasting: fill rates, time-to-fill, queue dynamics, and pricing a block from a forecast of absorption

### Takeaway
Empirically, fill time depends strongly on limit price but not on order size (Lo-MacKinlay-Zhang). Hypothetical fills built from first-passage times or the trade tape are "very poor proxies" for real fills, so forecast absorption from our own resting fills. The queue-reactive model finds that market-order intensity at the best quote decays exponentially with the queue size there. Guéant's block-pricing formula turns a forecast absorption rate V into a closed-form risk-liquidity premium. For a linear passive unwind it implies that per-share price risk is sigma*sqrt(T_unwind/3), not sigma*sqrt(T_unwind).

### Cited Findings
- **Lo-MacKinlay-Zhang (JFE 2002).**
  - Survival-analysis models of time-to-first-fill and time-to-completion, estimated on actual limit orders.
  - "execution times are very sensitive to limit price and several other explanatory variables, but not sensitive to limit size".
  - "hypothetical limit-order executions, constructed either theoretically from first-passage times or empirically from transactions data, are very poor proxies for actual limit-order executions".
  - Covariates: limit price, size, spread, volatility.
  - [Lo, MacKinlay & Zhang, JFE 65 (2002) 31-71, MIT page](https://web.mit.edu/Alo/www/Papers/limit10.html)
- **Queue-reactive model (Huang-Lehalle-Rosenbaum).** Order flow at each limit is a Markov jump process whose intensities depend on queue sizes (in units of average event size, AES).
  - Market orders at Q1: "The rate decreases exponentially with the available volume at Q1 ... market participants 'rushing for liquidity' when liquidity is rare, and 'waiting for better price' when liquidity is abundant".
  - Insertion into an empty queue (a new best price inside the spread) has "a significantly smaller value at 0 ... Such action is often risky".
  - Cancellation at Q1 is increasing and concave in queue size, flat beyond about 25 AES. Traders "cancel their orders more quickly when they find themselves alone in the queue".
  - [Huang, Lehalle & Rosenbaum, arXiv:1312.0563](https://arxiv.org/abs/1312.0563)
- **State-dependent fill probabilities (Lokin-Yu).** Semi-analytical fill probabilities for orders at the best quotes and deeper, before the opposite best moves. Validated on FX spot data. "for even deeper price levels, the corresponding fill probabilities are typically negligible". — [Lokin & Yu, arXiv:2403.02572](https://arxiv.org/abs/2403.02572)
- **Guéant POV block pricing (Props 4-5).**
  - Setup: price dS = sigma dW - k*v dt (permanent impact); execution cost V*L(v/V) + psi*v with L(rho) = eta*rho^(1+phi); market volume V constant; CARA gamma.
  - Optimal participation rho* = (gamma*sigma^2*q0^2 / (6*eta*phi*V))^(1/(1+phi)), independent of psi and k.
  - Block price (certainty equivalent): P(q0) = q0*S0 - psi*q0 - k*q0^2/2 - (1+phi)*eta^(1/(1+phi))*(gamma*sigma^2/(6*phi*V))^(phi/(1+phi))*q0^((1+3*phi)/(1+phi)).
  - Proof step: J(rho) = eta*rho^phi*q0 + (gamma/6)*sigma^2*q0^3/(rho*V). The second term is the CARA risk premium of a constant-rate unwind at speed rho*V.
  - [Guéant, arXiv:1210.7608](https://arxiv.org/abs/1210.7608)
- **IS vs POV.** l_IS(q0)/l_POV(q0) >= ((1+phi)/(1+3*phi))*3^(phi/(1+phi)) >= e*log(3)/(2*sqrt(3)) ≈ 0.86. A constant-participation unwind therefore costs at most about 14% more risk-liquidity premium than the optimal time-unconstrained IS schedule. — [Guéant, arXiv:1210.7608](https://arxiv.org/abs/1210.7608)

### Inferences
- **Rule C (tender valuation from forecast absorption, replacing the visible-book x2 walk).**
  - Value per share = mid ± (passive exit offset: we rest at the touch, so we earn about half the spread; minus the expected share crossed times (half-spread + fee)) - gamma*sigma^2*q0^2/(6*a_hat), where a_hat is the forecast absorption in shares/s. This is Guéant's J term with rho*V = a_hat.
  - The certainty-equivalent risk charge is cubic in q0 in dollars (quadratic per share). Its standard-deviation form is sigma*q0*sqrt(T_u/3), with T_u = q0/a_hat.
  - Our current premium per config is risk_aversion x GARCH sigma x sqrt(unwind ticks). If "unwind ticks" is the full time to finish, that charge is sqrt(3) ≈ 1.73x the sd of a linear unwind's average-price slippage.
  - This could explain part of the measured $0.19/share pessimism, together with the x2 book walk, which ignores passive capture. Test this before believing it.
- **Rule D (forecasting a_hat).**
  - LMZ says forecast from our own realised resting fills, conditioned on the price level relative to the touch. Do not use the tape's traded volume or a first-passage model.
  - The existing `liq_prior` (from our 14 blocks) plus an EWMA is the right kind of estimator.
  - The queue-reactive result adds one covariate worth testing: our own displayed size. Huge displayed size at the best may slow the market orders that absorb us ("waiting for better price when liquidity is abundant").
  - Test: shown 1k vs 5k vs 10k, same hidden total, measuring the absorbed shares/s. Expect no effect against RIT's scripted bots and a possible effect against real competitors.
- **Rule E (slowdown as risk, not as signal).**
  - The remaining risk of a passive unwind is Var ≈ sigma^2*q^3/(3*a_hat). When a_hat drops by a factor f, the variance rises by 1/f. Going from 3,000 to 800 sh/s means 3.75x the variance, or 1.9x the sd.
  - Recomputing the hold/cross budget with the live a_hat is direction-free. It crosses more only because the time-at-risk lengthened.
  - This is the Q1 Rule A logic expressed as a budget.

### Gaps
- I did not obtain LMZ's survival distribution or coefficient tables (only the abstract page was accessible), so no quantitative elasticities are quoted.
- No source quantified how fill-rate collapses co-move with adverse price moves for a resting seller. This adverse-selection link is plausible but unsourced here.
- I could not verify the 2023 deep-learning survival models of fill time (e.g. Arroyo, Cartea et al.); not cited.

---

## Q4. Risk-neutral vs risk-averse; when holding to a known (free) close beats unwinding; terminal-penalty structures

### Takeaway
In every model above, the incentive to trade before T comes only from (i) risk aversion gamma or a running inventory penalty phi, and (ii) the terminal liquidation cost b or alpha. With a martingale price, b = 0 and gamma = 0, the optimal speed is zero and the optimal ask quote is unboundedly high. Any unwinding then costs money in expectation. Once gamma > 0, the trade-off is execution cost vs sigma^2 x integral of q(t)^2 dt. So the right control is a variance budget, which we already have as `hold_risk_budget`, scaled by remaining time and by the live fill rate.

### Cited Findings
- **GLFT terminal condition.** The terminal condition is u(T,x,q,s) = -exp(-gamma*(x + q*(s - b))), and the terminal quote is -b + (1/gamma)*ln(1+gamma/k). In the risk-neutral limit, quotes "increase with no upper bound" as T grows. — [GLFT 2012](https://arxiv.org/abs/1106.3279)
- **Cartea-Jaimungal-style quadratic terminal penalty.**
  - Objective: E[C_T - phi*int X_s^2 ds + X_T*(P_T - rho_pen*X_T)], with linear temporary impact S_t = P_t - kappa*r_t.
  - Value-function quadratic coefficient (Lehalle-Neuman Prop. 3.1, restating Cartea-Jaimungal): v2(t) = sqrt(kappa*phi)*(1 + zeta*e^(2*beta*(T-t)))/(1 - zeta*e^(2*beta*(T-t))).
  - [Lehalle & Neuman, arXiv:1704.00847](https://arxiv.org/abs/1704.00847)
  - Optimal speed with phi = 0: nu*_t = Q_t/(T - t + k/alpha), where k is the temporary-impact coefficient and alpha the terminal penalty. Near the horizon the strategy behaves like TWAP. I saw this formula in search-result text from Jaimungal's course notes ([UofT notes](https://www.utstat.toronto.edu/sjaimung/courses/sta4505/notes/2014/OptimalLiquidationNotes.pdf)). The PDF itself would not download, so treat the exact form as needing confirmation. It is also what the Riccati above gives at phi = 0 (my own derivation).
- **Guéant POV.** The only force that speeds execution is gamma*sigma^2: rho* is proportional to (gamma*sigma^2)^(1/(1+phi)), so rho* = 0 at gamma = 0. — [Guéant, arXiv:1210.7608](https://arxiv.org/abs/1210.7608)
- **OW.** A risk-neutral trader with a resilient book still trades at a finite speed, but only because completion by T is imposed. A risk-averse trader "will trade more aggressively at the beginning". — [Obizhaeva & Wang](https://www.nber.org/papers/w11444)
- **Bulthuis et al.** Insufficient liquidation penalties make the value function blow up beyond a finite horizon, i.e. the models need a terminal penalty to force trading. — [Bulthuis et al., arXiv:1604.04963](https://ar5iv.labs.arxiv.org/html/1604.04963)

### Inferences
- **Holding is optimal here.** With alpha = 0 (free close at the last trade), nu* = Q/(T - t + k/0) = 0. Absent risk aversion, never pay to unwind; the CJ result says exactly that.
- **Why we still unwind.** There are three legitimate reasons, all outside a pure risk-neutral execution model:
  - Risk preference over heat P&L. This depends on how RITC scores: rank-based scoring makes the tails matter.
  - Option value of limit room for future tenders. Holding 55k blocks half the 100k net limit; the config notes that the 55k block "crowds fixed tenders out of the net limit".
  - The close is the last traded price, so our own late crossing would mark down the remaining position.
- **Rule F (price limit room explicitly).** The marginal value of unwinding now = expected profit of tenders we could not otherwise accept in the remaining time x P(they arrive) - unwind cost. When few tender-seconds remain, this falls toward 0, which is a principled version of `close_hold_ticks`. Estimate it from logged tender arrival rates and per-tender edge. This is not a price forecast.
- **Rule G (time-scaled variance budget).** The CARA-optimal trade-off makes the holding penalty proportional to gamma*sigma^2*int q^2 dt. The current budget |q|*sigma*sqrt(ticks left) is the sd of holding q to the bell. Using the passive-unwind sd sigma*sqrt(q^3/(3*a_hat)) when a_hat*ticks_left > q, and the holding sd otherwise, would make the budget respond to liquidity (Rule E) at no extra direction assumption.

### Gaps
- No paper found that models a terminal close at the last traded price, where the close itself is impacted by the trader. All models use the unaffected mid or a fixed cost b.
- Not researched: competition scoring (rank vs P&L) and how it maps to an effective gamma.

---

## Q5. Which rules reduce losses in slow-unwind episodes without predicting price direction

### Takeaway
Under a martingale price, no execution rule changes expected P&L except through costs. The two big losses (absorption fell to 700-900 sh/s while price moved about 3 sd against us) are variance events, not mean events. Direction-free rules can only trade expected cost for smaller tails. The literature supports three such rules, each conditioned on observable liquidity rather than on price:
- a liquidity-scaled crossing threshold, q* proportional to sqrt(A_hat)/sigma (GLFT asymptotic quote = 0);
- a variance budget using the live absorption rate (Guéant J term / CJ running penalty);
- refill-rate-sized sweeps (OW/AFS).

A test should show reduced worst-heat loss and dispersion at a small mean cost. If it also shows a mean gain, suspect the simulator has momentum.

### Cited Findings
- **Crossing threshold.** Optimal depth turns negative ("send a market order") when inventory is large relative to the liquidity A and the time left, with risk aversion and volatility high. The long-horizon depth is (1/k)*ln(A/((k+gamma)*(0.5*gamma*sigma^2*q^2 - mu*q))), which is direction-free when mu = 0. — [GLFT 2012](https://arxiv.org/abs/1106.3279)
- **Risk-liquidity premium.** The premium of a constant-rate unwind is (gamma/6)*sigma^2*q0^3/(rho*V). It is inversely proportional to the absorption rate rho*V, so a drop in absorption raises the premium (and the optimal participation rises). — [Guéant, arXiv:1210.7608](https://arxiv.org/abs/1210.7608)
- **Resilience.** The optimal flow keeps book depletion D constant, so replenishment is consumed at a steady rate. The fraction traded in blocks, 2/(rhoT+2), rises as resilience rho falls. — [Obizhaeva & Wang](https://www.nber.org/papers/w11444)
- **Measuring fills.** Real fill times are poorly proxied by tape or first-passage constructions, so measure absorption from our own orders. — [Lo, MacKinlay & Zhang](https://web.mit.edu/Alo/www/Papers/limit10.html)
- **Thin books.** Market-order arrival at the best decays exponentially with queue size, and participants "rush for liquidity" when it is scarce. Thin books are therefore not always slow to absorb; absorption depends on queue state, not only on volatility. — [Huang, Lehalle & Rosenbaum](https://arxiv.org/abs/1312.0563)

### Inferences
- **Recommended test set** (paired seeds t >= 2 plus holdout, per the project's fair-testing rule; report mean, worst seed, 5th percentile and sd of heat P&L):
  1. **Rule A/E.** Replace the fixed 30 s AC shortfall trigger with "cross q - q*(A_hat, sigma)", where A_hat is the EWMA of our own passive absorption (sh/s) over the last 5-10 s. Stress it with the sim's "scarce passive fills (flow 0.3)" scenario and with a regime switch that cuts absorption by 4x mid-unwind. Expectation: lower worst-heat loss, slightly lower mean in a pure random walk.
  2. **Rule C.** Tender value from mid + passive capture - gamma*sigma^2*q0^2/(6*a_hat) (or the sd form sigma*sqrt(T_u/3)) vs the current book walk x2. Check calibration against the measured $0.19/share pessimism on real-heat tenders before A/B. Expectation: more tenders accepted at small positive edge. Must re-check the limit-room crowding effect (Rule F).
  3. **Rule B.** Size any crossing by the measured refill rate rho (volume refilled since the last tick), not by 35% of visible depth. Low priority: OW/AFS say cost is insensitive when rho is very high or very low.
  4. **Display-size A/B (queue-reactive).** Iceberg shown 1k vs 5k, measuring a_hat. Only meaningful in real heats with other traders; the RIT scripted flow is probably size-blind.
  5. **Post deeper with small q (GLFT monotonicity).** When q < q*/3 and time is ample, rest 1-2 ticks behind the touch to earn more spread.
- **Rejected-ideas consistency.** These rules are consistent with the user's rejections: they use no toxicity, direction or volatility-regime forecast. GARCH sigma enters only as a scale, and the value-of-information test found vol forecasts worth about $0, so using a constant or realised sigma should be equivalent.

### Gaps
- None of the opened papers studied an unwind where fill-rate collapse and adverse price moves are jointly generated. Whether a liquidity-scaled cross would have improved the two real losses (rather than locking in part of them earlier) cannot be determined from the literature. It needs replay on the recorded heats.
- The Cartea-Jaimungal-Penalva book, Guéant's "Financial Mathematics of Market Liquidity" and Lehalle-Laruelle were not accessible. Their MO-boundary and practitioner fill-rate tables are not covered here.
