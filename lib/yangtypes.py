"""
Copyright 2015, Rob Shakir (rjs@jive.com, rjs@rob.sh)

This project has been supported by:
          * Jive Communications, Inc.
          * BT plc.

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.
"""
import numpy as np
from decimal import Decimal
from bitarray import bitarray
import uuid
import re
import collections
import copy

NUMPY_INTEGER_TYPES = [np.uint8, np.uint16, np.uint32, np.uint64,
                    np.int8, np.int16, np.int32, np.int64]

# Words that could turn up in YANG definition files that are actually
# reserved names in Python, such as being builtin types. This list is
# not complete, but will probably continue to grow.
reserved_name = ["list", "str", "int", "global", "decimal", "float",
                  "as", "if", "else", "elsif", "map", "set", "class",
                  "from", "import", "pass", "return", "is", "exec",
                  "pop", "insert", "remove", "add", "delete", "local",
                  "get", "default", "yang_name"]

def is_yang_list(arg):
  if isinstance(arg, list):
    return True
  elif hasattr(arg, "_pybind_generated_by"):
    pygen = getattr(arg, "_pybind_generated_by")
    if pygen in ["TypedListType", "YANGListType"]:
      return True
  return False

def is_yang_leaflist(arg):
  pygen = getattr(arg, "_pybind_generated_by", None)
  if pygen is None:
    return False
  elif pygen == "TypedListType":
    return True
  return False

def remove_path_attributes(p):
  new_path = []
  for i in p:
    if "[" in i:
      new_path.append(i.split("[")[0])
    else:
      new_path.append(i)
  return new_path

def safe_name(arg):
  """
    Make a leaf or container name safe for use in Python.
  """
  k = arg
  arg = arg.replace("-", "_")
  arg = arg.replace(".", "_")
  if arg in reserved_name:
    arg += "_"
  # store the unsafe->original version mapping
  # so that we can retrieve it when get() is called.
  return arg

def RestrictedPrecisionDecimalType(*args, **kwargs):
  """
    Function to return a new type that is based on decimal.Decimal with
    an arbitrary restricted precision.
  """
  precision = kwargs.pop("precision", False)
  class RestrictedPrecisionDecimal(Decimal):
    """
      Class extending decimal.Decimal to restrict the precision that is
      stored, supporting the fraction-digits argument of the YANG decimal64
      type.
    """
    _precision = 10.0**(-1.0*int(precision))
    def __new__(self, *args, **kwargs):
      """
        Overloads the decimal __new__ function in order to round the input
        value to the new value.
      """
      if not self._precision is None:
        if len(args):
          value = Decimal(args[0]).quantize(Decimal(str(self._precision)))
        else:
          value = Decimal(0)
      elif len(args):
        value = Decimal(args[0])
      else:
        value = Decimal(0)
      obj = Decimal.__new__(self, value, **kwargs)
      return obj
  return type(RestrictedPrecisionDecimal(*args, **kwargs))

def RestrictedClassType(*args, **kwargs):
  """
    Function to return a new type that restricts an arbitrary base_type with
    a specified restriction. The restriction_type specified determines the
    type of restriction placed on the class, and the restriction_arg gives
    any input data that this function needs.
  """
  base_type = kwargs.pop("base_type", unicode)
  restriction_type = kwargs.pop("restriction_type", None)
  restriction_arg = kwargs.pop("restriction_arg", None)
  restriction_dict = kwargs.pop("restriction_dict", None)

  # this gives deserialisers some hints as to how to encode/decode this value
  # it must be a list since a restricted class can encapsulate a restricted
  # class
  current_restricted_class_type = re.sub("<(type|class) '(?P<class>.*)'>", "\g<class>", str(base_type))
  if hasattr(base_type, "_restricted_class_base"):
    restricted_class_hint = getattr(base_type, "_restricted_class_base")
    restricted_class_hint.append(current_restricted_class_type)
  else:
    restricted_class_hint = [current_restricted_class_type]

  class RestrictedClass(base_type):
    """
      A class that restricts the base_type class with a new function that the
      input value is validated against before being applied. The function is
      a static method which is assigned to _restricted_test.
    """
    __slots__ = ('_restricted_class_base')
    _pybind_generated_by = "RestrictedClassType"

    _restricted_class_base = restricted_class_hint


    def __init__(self, *args, **kwargs):
      """
        Overloads the base_class __init__ method to check the input argument
        against the validation function - returns on instance of the base_type
        class, which can be manipulated as per a usual Python object.
      """
      try:
        self.__check(args[0])
      except IndexError:
        pass
      super(RestrictedClass, self).__init__(*args, **kwargs)

    def __new__(self, *args, **kwargs):
      self._restriction_dict = restriction_dict
      self._restriction_tests = []

      """
        Create a new class instance, and dynamically define the
        _restriction_test method so that it can be called by other functions.
      """
      def convert_regexp(pattern):
        if not pattern[0] == "^":
          pattern = "^%s" % pattern
        if not pattern[len(pattern)-1] == "$":
          pattern = "%s$" % pattern
        return pattern

      def in_range_check(low_high_tuples):
        for check_tuple in low_high_tuples:
          all_none = True
          for i in range(0,len(check_tuple)):
            if check_tuple[i] is not None:
              all_none = False
          if all_none:
            raise AttributeError("Cannot specify a range that is all max and min")
        def range_check(value):
          range_results = []
          for check_tuple in low_high_tuples:
            chk = True
            if len(check_tuple) == 2:
              if check_tuple[0] is not None and value < check_tuple[0]:
                chk = False
              if check_tuple[1] is not None and value > check_tuple[1]:
                chk = False
            elif len(check_tuple) == 1:
              if value != float(check_tuple[0]):
                chk = False
            else:
              raise AttributeError("Invalid check tuple length specified")
            range_results.append(chk)
          return True in range_results
        return range_check

      def match_pattern_check(regexp):
        #return lambda i: True if re.compile(convert_regexp(regexp)).match(i) else False
        def mp_check(value):
          if not isinstance(value, basestring):
            return False
          if re.match(convert_regexp(regexp), value):
            return True
          return False
        return mp_check

      def in_dictionary_check(dictionary):
        return lambda i: unicode(i) in dictionary

      def length_check(maxlength, minlength=None):
        if maxlength is None and minlength is None:
          raise AttributeError("invalid means of specifying length check")
        def lgt_check(value):
          if maxlength is not None and len(value) > int(maxlength):
            return False
          if minlength is not None and len(value) < int(minlength):
            return False
          return True
        return lgt_check

      range_regex = re.compile("(?P<low>\-?[0-9\.]+)([ ]+)?\.\.([ ]+)?(?P<high>(\-?[0-9\.]+|max))")
      range_single_value_regex = re.compile("(?P<value>\-?[0-9\.]+)")

      val = False
      try:
        val = args[0]
      except IndexError:
        pass
      if self._restriction_dict is None:
        if restriction_type is not None and restriction_arg is not None:
          self._restriction_dict = {restriction_type: restriction_arg}
        else:
          raise ValueError, "must specify either a restriction dictionary or a type and argument"

      for rtype,rarg in self._restriction_dict.iteritems():
        if rtype == "pattern":
          tests = []
          self._restriction_tests.append(match_pattern_check(rarg))
        elif rtype == "range":
          ranges = []
          for range_spec in rarg:
            if range_regex.match(range_spec):
              low,high = range_regex.sub("\g<low>,\g<high>", range_spec).split(",")
              high = base_type(high) if not high == "max" else None
              low = base_type(low) if not low == "min" else None
              ranges.append((low,high))
            elif range_single_value_regex.match(range_spec):
              eqval = range_single_value_regex.sub('\g<value>', range_spec)
              eqval = base_type(eqval) if not eqval in ["max", "min"] else None
              ranges.append((eqval,))
          self._restriction_tests.append(in_range_check(ranges))
          if val:
            try:
              preval = val
              val = base_type(val)
              #
              # FIXME / TODO: numpy types overflow, so when we get a value that is actually
              # invalid here, we can accidentially make it invalid whilst casting it to the
              # right type. Needs some thought about whether numpy is the best option. Also
              # have open requests to remove numpy dependencies.
              #
              if not float(val) == float(preval):
                raise ValueError("Could not set value, overflowed when cast to native type")
            except:
              raise TypeError, "must specify a numeric type for a range argument"
        elif rtype == "length":
          if range_regex.match(rarg):
            minlength,maxlength = range_regex.sub('\g<low>,\g<high>', rarg).split(",")
            minlength = minlength if not minlength == "min" else None
            maxlength = maxlength if not maxlength == "max" else None
            self._restriction_tests.append(length_check(maxlength, minlength=minlength))
          else:
            self._restriction_tests.append(length_check(rarg))
        elif rtype == "dict_key":
          new_rarg = copy.deepcopy(rarg)
          # populate enum values
          used_values = []
          for k in new_rarg:
            if "value" in new_rarg[k]:
              used_values.append(int(new_rarg[k]["value"]))
          c = 0
          for k in new_rarg:
            while c in used_values:
              c += 1
            if not "value" in new_rarg[k]:
              new_rarg[k]["value"] = c
            c += 1
          self._restriction_tests.append(in_dictionary_check(new_rarg))
          self._enumeration_dict = new_rarg
        else:
          raise TypeError, "unsupported restriction type"

      if not val == False:
        for test in self._restriction_tests:
          passed = False
          if test(val):
            passed = True
            break
        if not passed:
          raise ValueError, "%s does not match a restricted type" % val

      obj = base_type.__new__(self, *args, **kwargs)
      return obj

    def __check(self, v):
      """
        Run the _restriction_test static method against the argument v,
        returning an error if the value does not validate.
      """
      v = base_type(v)
      for chkfn in self._restriction_tests:
        if not chkfn(v):
          raise ValueError, "did not match restricted type"
      return True

    def getValue(self, *args, **kwargs):
      """
        For types where there is a dict_key restriction (such as YANG
        enumeration), return the value of the dictionary key.
      """
      if "dict_key" in self._restriction_dict:
        value = kwargs.pop("mapped", False)
        if value:
          return self._enumeration_dict[self.__str__()]["value"]
      return self

  return type(RestrictedClass(*args, **kwargs))

def TypedListType(*args, **kwargs):
  """
    Return a type that consists of a list object where only
    certain types (specified by allowed_type kwarg to the function)
    can be added to the list.
  """
  allowed_type = kwargs.pop("allowed_type", unicode)
  if not isinstance(allowed_type, list):
    allowed_type = [allowed_type,]
  class TypedList(collections.MutableSequence):
    _pybind_generated_by = "TypedListType"
    _list = list()

    def __init__(self, *args, **kwargs):
      self._allowed_type = allowed_type
      self._list = list()
      if len(args):
        if isinstance(args[0], list):
          for i in args[0]:
            self.check(i)
          self._list.extend(args[0])
        else:
          self.check(args[0])
          self._list.append(args[0])

    def check(self,v):
      passed = False
      count = 0
      for i in self._allowed_type:
        if isinstance(v, i):
          passed = True
          break
        try:
          if hasattr(i, "_pybind_generated_by"):
            if getattr(i, "_pybind_generated_by") == "RestrictedClassType":
              tmp = i(v)
              passed = True
              break
            elif getattr(i, "_pybind_generated_by") == "ReferencePathType":
              tmp = i(v)
              passed = True
              break
          elif i == unicode and isinstance(v, str):
            tmp = unicode(v)
            passed = True
            break
          elif i in NUMPY_INTEGER_TYPES:
            # numpy has odd characteristics where
            # it supports lists, so we check against
            # int as well.
            tmp = int(v)
            tmp = i(v)
            passed = True
            break
        except (ValueError,TypeError):
          # ValueError is generated by restricted classes in general
          # TypeError is generated by int when it receives a [] which
          # is valid for numpy, but not for standard int.
          pass
        count += 1
      if not passed:
        raise ValueError("Cannot add %s to TypedList (accepts only %s)" % \
          (v, self._allowed_type))

    def __len__(self): return len(self._list)
    def __getitem__(self, i): return self._list[i]
    def __delitem__(self, i): del self._list[i]
    def __setitem__(self, i, v):
      self.check(v)
      self._list.insert(i,v)

    def insert(self, i, v):
      self.check(v)
      self._list.insert(i,v)

    def append(self, v):
      self.check(v)
      self._list.append(v)

    def __str__(self):
      return str(self._list)

    def __iter__(self):
      return iter(self._list)

    def __eq__(self, other):
      if self._list == other:
        return True
      return False

    def get(self, filter=False):
      return self._list
  return type(TypedList(*args,**kwargs))

def YANGListType(*args,**kwargs):
  """
    Return a type representing a YANG list, with a contained class.
    A dict or ordered dict is used to store the list, and the
    returned object behaves akin to a dictionary.

    Additional checks are performed to ensure that the keys of the
    list are valid before adding the value.

    .add(key) - initialises a new member of the list
    .delete(key) - removes it.

    Where a list exists that does not have a key - which can be the
    case for 'config false' lists - a uuid is generated and used
    as the key for the list.
  """
  try:
    keyname = args[0]
    listclass = args[1]
  except:
    raise TypeError, "A YANGList must be specified with a key value and a contained class"
  is_container = kwargs.pop("is_container", False)
  parent = kwargs.pop("parent", False)
  yang_name = kwargs.pop("yang_name", False)
  user_ordered = kwargs.pop("user_ordered", False)
  path_helper = kwargs.pop("path_helper", None)

  class YANGList(object):
    __slots__ = ('_pybind_generated_by', '_members', '_keyval', '_contained_class', '_path_helper')
    _pybind_generated_by = "YANGListType"

    def __init__(self, *args, **kwargs):
      if user_ordered:
        self._members = collections.OrderedDict()
      else:
        self._members = dict()
      self._keyval = keyname
      if not type(listclass) == type(int):
        raise ValueError, "contained class of a YANGList must be a class"
      self._contained_class = listclass
      self._path_helper = path_helper

    def __str__(self):
      return str(self._members)

    def __repr__(self):
      return repr(self._members)

    def __check__(self, v):
      if self._contained_class is None:
        return False
      try:
        tmp = YANGDynClass(base=self._contained_class, parent=parent, yang_name=yang_name,
                         is_container=is_container, path_helper=False)
        if not tmp.__slots__ == v.__slots__:
          return False
      except:
        return False
      return True

    def iteritems(self):
      return self._members.iteritems()

    def __iter__(self):
      return iter(self._members)

    def __contains__(self,k):
      if k in self._members:
        return True
      return False

    def __getitem__(self, k):
      return self._members[k]

    def __setitem__(self, k, v):
      self.__set(k,v)

    def __set(self, k=False, v=False):
      update=False
      if v and not self.__check__(v):
        raise ValueError, "value must be set to an instance of %s" % \
          (self._contained_class)
      if k in self._members:
        update=True
      if self._keyval:
        try:
          tmp = YANGDynClass(base=self._contained_class, parent=parent, yang_name=yang_name,
                              is_container='container', path_helper=False)
          if " " in self._keyval:
            keys = self._keyval.split(" ")
            keyparts = k.split(" ")
            if not len(keyparts) == len(keys):
              raise KeyError, "YANGList key must contain all key elements (%s)" % (self._keyval.split(" "))
            path_keystring = "["
            for kv,kp in zip(keys,keyparts):
              kv_obj = getattr(tmp, kv)
              path_keystring += "%s='%s' " % (kv_obj.yang_name(),kp)
            path_keystring = path_keystring.rstrip(" ")
            path_keystring += "]"
          else:
            if k == "":
              raise KeyError("Cannot set a null key for a list entry!")
            keys = [self._keyval,]
            keyparts = [k,]
            kv_obj = getattr(tmp, self._keyval)
            path_keystring = "[%s=%s]" % (kv_obj.yang_name(), k)

          if not update:
            tmp = YANGDynClass(base=self._contained_class, parent=parent, yang_name=yang_name, \
                  is_container='container', path_helper=path_helper, \
                  register_path=self._parent._path() + [self._yang_name+path_keystring], \
                  extmethods=self._parent._extmethods)
          else:
            # hand the value to the init, rather than simply creating an empty object.
            tmp = YANGDynClass(v, base=self._contained_class, parent=parent, yang_name=yang_name, \
                  is_container='container', path_helper=path_helper, \
                  register_path=self._parent._path() + [self._yang_name+path_keystring],
                  extmethods=self._parent._extmethods)

          for i in range(0,len(keys)):
            key = getattr(tmp, "_set_%s" % keys[i])
            key(keyparts[i])
          self._members[k] = tmp
        except ValueError, m:
          raise KeyError, "key value must be valid, %s" % m
      else:
        # this is a list that does not have a key specified, and hence
        # we generate a uuid that is used as the key, the method then
        # returns the uuid for the upstream process to use
        k = str(uuid.uuid1())
        self._members[k] = YANGDynClass(base=self._contained_class, parent=parent, yang_name=yang_name, \
                            is_container=is_container, path_helper=path_helper, extmethods=self._parent._extmethods)
        return k

    def __delitem__(self, k):
      del self._members[k]

    def __len__(self): return len(self._members)

    def keys(self): return self._members.keys()

    def add(self, k=None):
      if k in self._members:
        raise KeyError, "%s is already defined as a list entry" % k
      if self._keyval:
        if k is None:
          raise KeyError, "a list with a key value must have a key specified"
        self.__set(k)
      else:
        k = self.__set()
        return k

    def delete(self, k):
      if self._path_helper:
        current_item = self._members[k]
        if " " in self._keyval:
          keyparts = self._keyval.split(" ")
          keyargs = k.split(" ")
          key_string = "["
          for key,val in zip(keyparts,keyargs):
            kv_o = getattr(self._members[k], key)
            key_string += "%s=%s " % (kv_o.yang_name(),val)
          key_string = key_string.rstrip(" ")
          key_string += "]"
        else:
          kv_o = getattr(self._members[k], self._keyval)
          key_string = "[@%s=%s]" % (kv_o.yang_name(), k)

        obj_path = self._parent._path() + [self._yang_name + key_string]

      try:
        del self._members[k]
        if self._path_helper:
          self._path_helper.unregister(obj_path)
      except KeyError, m:
        raise KeyError, "key %s was not in list (%s)" % (k,m)

    def get(self, filter=False):
      if user_ordered:
        d = collections.OrderedDict()
      else:
        d = {}
      for i in self._members:
        if hasattr(self._members[i], "get"):
          d[i] = self._members[i].get(filter=filter)
        else:
          d[i] = self._members[i]
      return d

  return type(YANGList(*args,**kwargs))

class YANGBool(int):
  """
    A custom boolean class for using in YANG. Since bool has specific
    logic in python, it is not possible to extend the existing bool
    objects.

    This bool also accepts input matching strings to handle the
    forms that might be used in YANG modules.
  """
  def __new__(self, *args, **kwargs):
    false_args = ["false", "False", False, 0, "0"]
    true_args = ["true", "True", True, 1, "1"]
    if len(args):
      if not args[0] in false_args + true_args:
        raise ValueError, "%s is an invalid value for a YANGBool" % args[0]
      value = 0 if args[0] in false_args else 1
    else:
      value = 0
    return int.__new__(self, bool(value))

  def __repr__(self):
    return str([False, True][self])

  def __str__(self):
    return str(self.__repr__())

def YANGDynClass(*args,**kwargs):
  """
    Wrap an type - specified in the base_type arugment - with
    a set of custom attributes that YANG specifies (or are required
    for serialisation of that object). Particularly:

      - base_type:  the original type - for example, string, int.
      - default:    the YANG specified default value of the type.
      - yang_name:  the YANG name of the type (as opposed to a 'safe'
                    Python version).
      - parent:     the class which this type is a member of in the
                    YANG-specified tree.
      - choice:     The choice branch that this type is a member of.
      - is_{container,leaf}: whether this element is a container or
                             a leaf.
      - path_helper: pyangbind helper class to allow XPATH lookups.
      - supplied_register_path: an override for the path that this
                                object should register to. This is
                                used when an element is a member of
                                a list to add the key attributes to
                                the path.
      - extensions:  The list of extensions that should be stored
                     with the type.
  """
  base_type = kwargs.pop("base", False)
  default = kwargs.pop("default", False)
  yang_name = kwargs.pop("yang_name", False)
  parent_instance = kwargs.pop("parent", False)
  choice_member = kwargs.pop("choice", False)
  is_container = kwargs.pop("is_container", False)
  is_leaf = kwargs.pop("is_leaf", False)
  path_helper = kwargs.pop("path_helper", None)
  supplied_register_path = kwargs.pop("register_path", None)
  extensions = kwargs.pop("extensions", None)
  extmethods = kwargs.pop("extmethods", None)
  is_keyval = kwargs.pop("is_keyval", False)
  if not base_type:
    raise TypeError, "must have a base type"
  if base_type in NUMPY_INTEGER_TYPES and len(args):
    if isinstance(args[0], list):
      raise TypeError, "do not support creating numpy ndarrays!"
  if isinstance(base_type, list):
    # this is a union, we must infer type
    if not len(args):
      # there is no argument to infer the type from
      # so use the first type (default)
      base_type = base_type[0]
    else:
      type_test = False
      for candidate_type in base_type:
        try:
          type_test = candidate_type(args[0]) # does the slipper fit?
          break
        except:
          pass # don't worry, move on, plenty more fish (types) in the sea...
      if not type_test:
        # we're left alone at midnight -- no types fit the arguments
        raise TypeError, "did not find a valid type using the argument as a" + \
                            "hint"
      # otherwise, hop, skip and jump with the last candidate
      base_type = candidate_type

  class YANGBaseClass(base_type):
    if is_container:
      __slots__ = ('_default', '_mchanged', '_yang_name', '_choice', '_parent',
                   '_supplied_register_path', '_path_helper', '_base_type', '_is_leaf',
                   '_is_container', '_extensionsd', '_pybind_base_class', '_extmethods',
                   '_is_keyval')

    _pybind_base_class = re.sub("<(type|class) '(?P<class>.*)'>", "\g<class>", str(base_type))

    def __new__(self, *args, **kwargs):
      obj = base_type.__new__(self, *args, **kwargs)
      return obj

    def __init__(self, *args, **kwargs):
      self._default = False
      self._mchanged = False
      self._yang_name = yang_name
      self._parent = parent_instance
      self._choice = choice_member
      self._path_helper = path_helper
      self._supplied_register_path = supplied_register_path
      self._base_type = base_type
      self._is_leaf = is_leaf
      self._is_container = is_container
      self._extensionsd = extensions
      self._extmethods = extmethods
      self._is_keyval = is_keyval

      if default:
        self._default = default
      if len(args):
        if not args[0] == self._default:
          self._set()

      # lists themselves do not register, only elements within them
      # are actually created in the tree.
      if not self._is_container == "list":
        if self._path_helper:
          if self._supplied_register_path is None:
            self._path_helper.register(self._register_path(), self)
          else:
            self._path_helper.register(self._supplied_register_path, self)

      if self._is_container == 'list' or self._is_container == 'container':
        kwargs['path_helper'] = self._path_helper

      try:
        super(YANGBaseClass, self).__init__(*args, **kwargs)
      except Exception as e:
        raise TypeError, "couldn't generate dynamic type -> %s -> %s" % (type(e),e)

    def _changed(self):
      return self._mchanged

    def _extensions(self):
      return self._extensionsd

    def _path(self):
      return self._register_path()

    def _yang_path(self):
      return "/"+"/".join(self._register_path())

    def __str__(self):
      return super(YANGBaseClass, self).__str__()

    def __repr__(self):
      return super(YANGBaseClass, self).__repr__()

    def _set(self,choice=False):
      if hasattr(self, '__choices__') and choice:
        for ch in self.__choices__:
          if ch == choice[0]:
            for case in self.__choices__[ch]:
              if not case == choice[1]:
                for elem in self.__choices__[ch][case]:
                  method = "_unset_%s" % elem
                  if not hasattr(self, method):
                    raise AttributeError, "unmapped choice!"
                  x = getattr(self, method)
                  x()
      if self._choice and not choice:
        choice=self._choice
      self._mchanged = True
      if self._parent and hasattr(self._parent, "_set"):
        self._parent._set(choice=choice)

    def yang_name(self):
      return self._yang_name

    def default(self):
      return self._default

    # we need to overload the set methods
    def __setitem__(self, *args, **kwargs):
      self._set()
      super(YANGBaseClass, self).__setitem__(*args, **kwargs)

    def append(self, *args, **kwargs):
      if not hasattr(super(YANGBaseClass,self), "append"):
        raise AttributeError("%s object has no attribute append" % base_type)
      self._set()
      super(YANGBaseClass, self).append(*args,**kwargs)

    def pop(self, *args, **kwargs):
      if not hasattr(super(YANGBaseClass, self), "pop"):
        raise AttributeError("%s object has no attribute pop" % base_type)
      self._set()
      item = super(YANGBaseClass, self).pop(*args, **kwargs)
      return item

    def remove(self, *args, **kwargs):
      if not hasattr(super(YANGBaseClass, self), "remove"):
        raise AttributeError("%s object has no attribute remove" % base_type)
      self._set()
      if self._path_helper:
        elem_index = super(YANGBaseClass, self).index(*args, **kwargs)
        item = super(YANGBaseClass, self).__getitem__(elem_index)
      super(YANGBaseClass, self).remove(*args, **kwargs)

    def extend(self, *args, **kwargs):
      if not hasattr(super(YANGBaseClass, self), "extend"):
        raise AttributeError("%s object has no attribute extend" % base_type)
      self._set()
      super(YANGBaseClass, self).extend(*args, **kwargs)


    def insert(self, *args, **kwargs):
      if not hasattr(super(YANGBaseClass,self), "insert"):
        raise AttributeError("%s object has no attribute insert" % base_type)
      self._set()
      super(YANGBaseClass, self).insert(*args, **kwargs)

    def _register_path(self):
      if not self._supplied_register_path is None:
        return self._supplied_register_path
      if not self._parent is None:
        return self._parent._path() + [self._yang_name]
      else:
        return []

    def __has_extmethod(self, method):
      if not self._extmethods:
        return False
      chk_path = "/" + "/".join(remove_path_attributes(self._register_path()))
      if chk_path in self._extmethods:
        if hasattr(self._extmethods[chk_path], method):
          return getattr(self._extmethods[chk_path], method, False)
        return False
      return False

    def __return_extmethod(self, method, *args, **kwargs):
      cm = self.__has_extmethod(method)
      if cm:
        kwargs['caller'] = self._register_path()
        kwargs['path_helper'] = self._path_helper
        return cm(*args, **kwargs)
      return None

    def _presave(self, *args, **kwargs):
      return self.__return_extmethod("presave", *args, **kwargs)

    def _commit(self, *args, **kwargs):
      return self.__return_extmethod("commit", *args, **kwargs)

    def _postsave(self, *args, **kwargs):
      return self.__return_extmethod("postsave", *args, **kwargs)

    def _validate(self, *args, **kwargs):
      return self.__return_extmethod("validate", *args, **kwargs)

  return YANGBaseClass(*args, **kwargs)

def ReferenceType(*args,**kwargs):
  """
    A type which based on a path provided acts as a leafref.
    The caller argument is used to allow the path that is provided
    to be a relative (rather than absolute) path. The require_instance
    argument specifies whether errors should be thrown in the case
    that the referenced instance does not exist.
  """
  ref_path = kwargs.pop("referenced_path", False)
  path_helper = kwargs.pop("path_helper", None)
  caller = kwargs.pop("caller", False)
  require_instance = kwargs.pop("require_instance", False)
  class ReferencePathType(object):

    __slots__ = ('_referenced_path', '_path_helper', '_caller', '_referenced_object',
                 '_ptr', '_require_instance', '_type')

    _pybind_generated_by = "ReferencePathType"

    def __init__(self, *args, **kwargs):
      self._referenced_path = ref_path
      self._path_helper = path_helper
      self._referenced_object = False
      self._caller = caller
      self._ptr = False
      self._require_instance = require_instance
      self._type = "unicode"

      if len(args):
        value = args[0]
      else:
        value = None

      if self._path_helper:
        path_chk = self._path_helper.get(self._referenced_path, caller=self._caller)

        # if the lookup returns only one leaf, then this means that we have something
        # that could potentially be a pointer. However, this is not sufficient to tell
        # whether it is (it could be a single list entry) - thus perform two additional
        # checks. 1) check whether this is the key value of a list (if it is then this
        # is something that can be externally referenced) and 2) check that this is not
        # a list itself (including a leaf-list)
        if len(path_chk) == 1 and not path_chk[0]._is_keyval and not is_yang_list(path_chk[0]):
          # we are not checking whether this leaf exists, but rather
          # this is a pointer to some other value.
          path_parts = self._referenced_path.split("/")
          leaf_name = path_parts[-1]
          if ":" in leaf_name:
            # normalise the namespace
            leaf_name = leaf_name.split(":")[1]
          set_method = getattr(path_chk[0]._parent, "_set_%s" % safe_name(leaf_name))
          get_method = getattr(path_chk[0]._parent, "_get_%s" % safe_name(leaf_name))
          if value:
            set_method(value)
          self._type = re.sub("<(type|class) '(?P<class>.*)'>", "\g<class>", str(get_method()._base_type))
          self._ptr = True
        elif self._require_instance:
          if not value:
            self._referenced_object = None
          else:
            found = False
            lookup_o = []
            path_chk = self._path_helper.get(self._referenced_path, caller=self._caller)

            if len(path_chk) == 1 and is_yang_leaflist(path_chk[0]):
              index = 0
              for i in path_chk[0]:
                if unicode(i) == unicode(value):
                  found = True
                  self._referenced_object = path_chk[0][index]
                  break
                index += 1
            else:
              found = False
              for i in path_chk:
                if unicode(i) == unicode(value):
                  found = True
                  self._referenced_object = i

            if not found:
              raise ValueError, "no such key (%s) existed in path (%s -> %s)" % (value, self._referenced_path, path_chk)
        else:
          # require instance is not set, so act like a string
          self._referenced_object = value

    def _get_ptr(self):
      if self._ptr:
        ptr = self._path_helper.get(self._referenced_path, caller=self._caller)
        if len(ptr) == 1:
          return ptr[0]
      raise ValueError, "Invalid pointer specified"

    def __repr__(self):
      if not self._ptr:
        return repr(self._referenced_object)
      return repr(self._get_ptr())

    def _get(self):
      if not self._ptr:
        return self._referenced_object
      return self._get_ptr()

    def __str__(self):
      if not self._ptr:
        return str(self._referenced_object)
      return str(self._get_ptr())

  return type(ReferencePathType(*args,**kwargs))
