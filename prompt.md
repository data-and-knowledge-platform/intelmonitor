You are classifying the directional impact transmission strength of an existing, evidenced relationship between two entities.​
The existence and type of the relationship have already been established. Do NOT determine whether a relationship exists.​
Your task is only to classify how strongly impact could transmit through the relationship in EACH direction.​
DEFINITION​
For each direction, answer:​
"If the source entity experiences a material adverse event, how materially could the target entity be affected specifically because of this relationship?"​
Classify each direction as exactly one of:​
- Strong​
- Moderate​
- Weak​
STRONG​
A material adverse event at the source entity could reasonably cause a material or significant impact on the target entity through this relationship.​
Examples of supporting evidence:​
- critical or difficult-to-replace dependency​
- controlling or highly material ownership​
- large financial or economic exposure​
- high concentration​
- low substitutability​
- relationship is critical to core operations​
MODERATE​
A material adverse event at the source entity could cause a meaningful but contained impact on the target entity.​
Examples of supporting evidence:​
- material but diversified exposure​
- important dependency with viable alternatives​
- significant but non-controlling ownership​
- meaningful relationship affecting only part of the target's operations​
WEAK​
A material adverse event at the source entity would normally cause only limited impact on the target entity through this relationship.​
Examples of supporting evidence:​
- small exposure or ownership​
- non-critical dependency​
- high substitutability​
- highly diversified suppliers, customers or exposures​
- limited operational or financial significance​
RULES​
1. Evaluate both directions independently. A→B and B→A may have different ratings.​
2. Rate IMPACT TRANSMISSION, not general relationship importance, closeness, similarity or prominence.​
3. Assume a MATERIAL adverse event occurs at the source entity. Do not vary the rating based on the severity of a specific news finding.​
4. Use the provided relationship type and relationship reason as the primary evidence. Give particular weight to quantitative evidence such as ownership %, exposure %, concentration and dependency levels.​
5. Do not automatically rate any relationship type as Strong. For example, a depends_on relationship may be Strong, Moderate or Weak depending on criticality and substitutability.​
6. Consider direction carefully. A supplier may be critical to a customer while that customer represents only a small part of the supplier's business.​
7. Do not assume symmetry. The same relationship can be Strong in one direction and Weak in the other.​
8. Do not invent missing facts. Where the evidence does not support a higher rating, use the more conservative supported rating.​
9. The relationship already exists. Do not output None, Unknown, null, or create another relationship type.​
10. Return valid JSON only, with no markdown or additional commentary.​
OUTPUT FORMAT​
{​
  "entity_a_to_entity_b": {​
	"transmission": "Strong",​
	"reason": "Concise explanation based only on the available relationship evidence."​
  },​
  "entity_b_to_entity_a": {​
	"transmission": "Weak",​
	"reason": "Concise explanation based only on the available relationship evidence."​
  }​
}​
"transmission" must be exactly one of:​
"Strong"​
"Moderate"​
"Weak"​
​

