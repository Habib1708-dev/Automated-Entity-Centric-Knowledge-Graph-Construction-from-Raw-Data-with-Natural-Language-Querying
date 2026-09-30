# Judging free-text answers of `kg qa` (R71)

The judge decides only the questions whose expected answer is a short text; sets and numbers are scored by
code. Every verdict has a one-line `reason`. Examples below are invented, never from a judged dataset.

1. **Correct by meaning.** An answer is correct when it states what the expected answer states, in any
   wording. Extra detail is fine when it is true of the source and contradicts nothing ("A refund of the
   delivery fee, offered by phone" for the expected "A refund of the delivery fee").
2. **Every part the expected answer has.** When the expected answer has two parts ("most find it quiet;
   one finds it loud at night"), an answer missing one part is wrong.
3. **Content, not form.** Judge what the answer says in whichever field it gives it: its text, else its
   list of names or its number. A free-text question answered as a list of names ("Harbour Garage, Leeds")
   is judged on that content.
4. **A partial value is wrong.** Only the year of an expected date, or a generic phrase for a specific one,
   does not answer the question ("2019" for "12 March 2019").
5. **No answer is wrong.** "The texts do not say" is wrong when the expected answer exists in the source.
6. Judge against the expected answer and its gold evidence quote; do not use knowledge from outside them.
